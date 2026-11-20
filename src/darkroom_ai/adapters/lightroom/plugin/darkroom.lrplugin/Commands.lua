--[[
Commands.lua - command handlers for the darkroom_ai bridge.

Every handler is called as handler(params, ctx) inside an LrTasks task and returns a result table.
Failures are raised with fail(code, message) and turned into error responses by Bridge.lua.

ctx = { config = <bridge.json table>, catalog = LrCatalog, catalog_path = string,
        writes_allowed = boolean }

Rules:
- Photos are addressed by UUID (catalog:findPhotoByUuid), never by the current selection.
- Every handler listed in Commands.write refuses to run unless the open catalog is the
  configured catalog (ctx.writes_allowed).
- SDK calls used here are documented in the Lightroom Classic 15.2 SDK API reference:
  LrPhoto (getDevelopSettings, applyDevelopSettings, createDevelopSnapshot, getDevelopSnapshots,
  applyDevelopSnapshot, getRawMetadata, getFormattedMetadata), LrCatalog (findPhotoByUuid,
  getTargetPhoto(s), setSelectedPhotos, createVirtualCopies, withWriteAccessDo, getPath),
  LrExportSession, LrExportRendition.
]]

local LrApplication = import "LrApplication"
local LrExportSession = import "LrExportSession"
local LrFileUtils = import "LrFileUtils"
local LrSelection = import "LrSelection"
local LrTasks = import "LrTasks"

local Json = require "Json"

local PLUGIN_VERSION = "0.1.0"
local PROTOCOL_VERSION = 1
local WRITE_ACCESS_TIMEOUT_S = 10

local Commands = { read = {}, write = {} }

local function fail(code, message)
	error({ code = code, message = message }, 0)
end

local function list(t)
	if #t == 0 then
		return Json.EMPTY_ARRAY
	end
	return t
end

local function findPhoto(ctx, id)
	if type(id) ~= "string" or id == "" then
		fail("INVALID_PARAMS", "photo_id (the photo's UUID string) is required")
	end
	local photo = ctx.catalog:findPhotoByUuid(id)
	if not photo then
		fail("PHOTO_NOT_FOUND", "No photo with id " .. id .. " in the open catalog")
	end
	return photo
end

local function summary(photo)
	local isCopy = photo:getRawMetadata("isVirtualCopy") and true or false
	local master = isCopy and photo:getRawMetadata("masterPhoto") or nil
	local dims = photo:getRawMetadata("dimensions")
	return {
		id = photo:getRawMetadata("uuid"),
		local_id = photo.localIdentifier,
		file_name = photo:getFormattedMetadata("fileName"),
		path = photo:getRawMetadata("path"),
		file_format = photo:getRawMetadata("fileFormat"),
		is_virtual_copy = isCopy,
		master_id = master and master:getRawMetadata("uuid") or nil,
		copy_name = photo:getFormattedMetadata("copyName"),
		width = dims and dims.width or nil,
		height = dims and dims.height or nil,
	}
end

local function pick(settings, keys)
	if type(keys) ~= "table" or #keys == 0 then
		return settings
	end
	local result = {}
	for _, key in ipairs(keys) do
		result[key] = settings[key]
	end
	return result
end

local function withWrite(ctx, actionName, func)
	local outcome = ctx.catalog:withWriteAccessDo(actionName, func, { timeout = WRITE_ACCESS_TIMEOUT_S })
	if outcome ~= "executed" then
		fail(
			"WRITE_ACCESS_TIMEOUT",
			"Lightroom did not grant catalog write access within "
				.. WRITE_ACCESS_TIMEOUT_S
				.. " s (result: "
				.. tostring(outcome)
				.. ")"
		)
	end
end

local function snapshots(photo)
	local result = {}
	for _, snap in ipairs(photo:getDevelopSnapshots() or {}) do
		result[#result + 1] = { id = snap.snapshotID, name = snap.name, global_id = snap.id_global }
	end
	return result
end

--------------------------------------------------------------------------------
-- Read commands

function Commands.read.ping(_, ctx)
	return {
		plugin_version = PLUGIN_VERSION,
		protocol = PROTOCOL_VERSION,
		lightroom_version = LrApplication.versionString(),
		catalog_path = ctx.catalog_path,
		configured_catalog = ctx.config.catalog_path,
		writes_allowed = ctx.writes_allowed,
	}
end

-- Echoes params back so the client can verify the JSON encoder/decoder inside Lightroom.
function Commands.read.selftest(params)
	return { echo = params.value }
end

-- Reports which SDK methods exist in this Lightroom version (needs one selected photo).
function Commands.read.probe(_, ctx)
	local photo = ctx.catalog:getTargetPhoto()
	local photoMethods = {
		"getDevelopSettings",
		"applyDevelopSettings",
		"createDevelopSnapshot",
		"getDevelopSnapshots",
		"applyDevelopSnapshot",
		"deleteDevelopSnapshot",
		"requestJpegThumbnail",
		"updateAISettings",
	}
	local catalogMethods = {
		"findPhotoByUuid",
		"getPhotoByLocalId",
		"createVirtualCopies",
		"setSelectedPhotos",
		"getPath",
		"withWriteAccessDo",
	}
	local result = { photo = {}, catalog = {}, has_target_photo = photo ~= nil }
	for _, name in ipairs(catalogMethods) do
		local ok, value = pcall(function()
			return ctx.catalog[name]
		end)
		result.catalog[name] = ok and type(value) == "function"
	end
	if photo then
		for _, name in ipairs(photoMethods) do
			local ok, value = pcall(function()
				return photo[name]
			end)
			result.photo[name] = ok and type(value) == "function"
		end
	end
	return result
end

-- The only command that looks at the selection: it tells the client which photo IDs to use.
function Commands.read.get_selection(_, ctx)
	local active = ctx.catalog:getTargetPhoto()
	if not active then
		return { active = nil, photos = Json.EMPTY_ARRAY }
	end
	local photos = {}
	for _, photo in ipairs(ctx.catalog:getTargetPhotos()) do
		photos[#photos + 1] = summary(photo)
	end
	return { active = summary(active), photos = list(photos) }
end

-- Looks a photo up by its file path, so the client can get an ID without using the selection.
-- Returns { found = false } when the file is not in the open catalog.
function Commands.read.find_photo(params, ctx)
	if type(params.path) ~= "string" or params.path == "" then
		fail("INVALID_PARAMS", "path (absolute file path) is required")
	end
	local photo = ctx.catalog:findPhotoByPath(params.path)
	if not photo then
		return { found = false }
	end
	return { found = true, photo = summary(photo) }
end

function Commands.read.get_photo(params, ctx)
	local photo = findPhoto(ctx, params.photo_id)
	local result = summary(photo)
	result.snapshots = list(snapshots(photo))
	if params.include_settings then
		result.settings = pick(photo:getDevelopSettings(), params.keys)
	end
	return result
end

function Commands.read.list_snapshots(params, ctx)
	return { snapshots = list(snapshots(findPhoto(ctx, params.photo_id))) }
end

--------------------------------------------------------------------------------
-- Write commands (only run when the open catalog is the configured catalog)

-- catalog:createVirtualCopies works on the current selection only, so the selection is saved,
-- set to the one target photo, verified, and restored afterwards.
function Commands.write.create_virtual_copy(params, ctx)
	local catalog = ctx.catalog
	local photo = findPhoto(ctx, params.photo_id)
	local targetId = photo.localIdentifier

	local active = catalog:getTargetPhoto()
	local others = {}
	if active then
		for _, p in ipairs(catalog:getTargetPhotos()) do
			if p.localIdentifier ~= active.localIdentifier then
				others[#others + 1] = p
			end
		end
	end

	local function restoreSelection()
		if active then
			catalog:setSelectedPhotos(active, others)
		else
			LrSelection.selectNone()
		end
	end

	-- Waits up to `seconds` for the selection to be exactly the target photo.
	local nowActive, nowSelected
	local function onlyTargetSelected(seconds)
		for _ = 1, math.floor(seconds * 10) do
			LrTasks.sleep(0.1)
			nowActive = catalog:getTargetPhoto()
			nowSelected = catalog:getTargetPhotos()
			if nowActive and nowActive.localIdentifier == targetId and #nowSelected == 1 then
				return true
			end
		end
		return false
	end

	-- Try the plain call first, then inside a write gate (which of the two Lightroom needs is
	-- not documented). The attempt that worked is reported in the result.
	local method = "direct"
	local directOk, directError = LrTasks.pcall(function()
		catalog:setSelectedPhotos(photo, {})
	end)
	local selected = directOk and onlyTargetSelected(1.5)
	local gateError
	if not selected then
		method = "write-gate"
		local gateOk
		gateOk, gateError = LrTasks.pcall(function()
			withWrite(ctx, "darkroom_ai: select photo", function()
				catalog:setSelectedPhotos(photo, {})
			end)
		end)
		selected = gateOk and onlyTargetSelected(1.5)
	end
	if not selected then
		pcall(restoreSelection)
		fail(
			"SELECTION_FAILED",
			"Could not select only the requested photo. No virtual copy was created. Details: target local id "
				.. tostring(targetId)
				.. ", active local id "
				.. tostring(nowActive and nowActive.localIdentifier)
				.. ", selected count "
				.. tostring(nowSelected and #nowSelected)
				.. ", direct call error "
				.. tostring(directError)
				.. ", write-gate error "
				.. tostring(type(gateError) == "table" and gateError.message or gateError)
		)
	end

	local ok, copies = LrTasks.pcall(function()
		return catalog:createVirtualCopies(params.copy_name)
	end)
	pcall(restoreSelection)
	if not ok then
		fail("VIRTUAL_COPY_FAILED", tostring(copies))
	end
	if type(copies) ~= "table" or #copies ~= 1 then
		fail("VIRTUAL_COPY_FAILED", "Expected exactly one virtual copy, got " .. tostring(copies and #copies))
	end
	local result = summary(copies[1])
	result.selection_method = method
	return result
end

function Commands.write.create_snapshot(params, ctx)
	local photo = findPhoto(ctx, params.photo_id)
	if type(params.name) ~= "string" or params.name == "" then
		fail("INVALID_PARAMS", "name is required")
	end
	local created
	withWrite(ctx, "darkroom_ai: create snapshot", function()
		created = photo:createDevelopSnapshot(params.name, false)
	end)
	local found
	for _, snap in ipairs(snapshots(photo)) do
		if snap.name == params.name then
			found = snap
		end
	end
	return { created = created and true or false, snapshot = found }
end

function Commands.write.apply_snapshot(params, ctx)
	local photo = findPhoto(ctx, params.photo_id)
	local target
	for _, snap in ipairs(snapshots(photo)) do
		if (params.snapshot_id and snap.id == params.snapshot_id) or (params.name and snap.name == params.name) then
			target = snap
		end
	end
	if not target then
		fail("SNAPSHOT_NOT_FOUND", "No matching snapshot on this photo")
	end
	withWrite(ctx, "darkroom_ai: apply snapshot", function()
		photo:applyDevelopSnapshot(target.id)
	end)
	return { applied = target }
end

function Commands.write.apply_settings(params, ctx)
	local photo = findPhoto(ctx, params.photo_id)
	local settings = params.settings
	if type(settings) ~= "table" or next(settings) == nil then
		fail("INVALID_PARAMS", "settings must be a non-empty object")
	end
	local keys = {}
	for key in pairs(settings) do
		if type(key) ~= "string" then
			fail("INVALID_PARAMS", "settings keys must be strings")
		end
		keys[#keys + 1] = key
	end
	local historyName = type(params.history_name) == "string" and params.history_name or "darkroom_ai edit"

	local before = pick(photo:getDevelopSettings(), keys)
	withWrite(ctx, "darkroom_ai: apply develop settings", function()
		photo:applyDevelopSettings(settings, historyName)
	end)
	local after = pick(photo:getDevelopSettings(), keys)
	return { before = before, after = after }
end

-- Exports a JPEG preview into the configured preview folder only (never an arbitrary path).
-- Existing files are never overwritten: Lightroom renames on collision and the real path is returned.
function Commands.write.export_preview(params, ctx)
	local photo = findPhoto(ctx, params.photo_id)
	local name = params.name
	if type(name) ~= "string" or not string.match(name, "^[%w_%-]+$") or #name > 80 then
		fail("INVALID_PARAMS", "name must be 1-80 characters of letters, digits, '_' or '-'")
	end
	local longEdge = tonumber(params.long_edge) or 1024
	if longEdge < 64 or longEdge > 4096 then
		fail("INVALID_PARAMS", "long_edge must be between 64 and 4096")
	end
	local quality = tonumber(params.quality) or 0.85
	if quality < 0.1 or quality > 1 then
		fail("INVALID_PARAMS", "quality must be between 0.1 and 1")
	end

	local folder = ctx.config.preview_dir
	if LrFileUtils.exists(folder) ~= "directory" then
		LrFileUtils.createAllDirectories(folder)
	end
	if LrFileUtils.exists(folder) ~= "directory" then
		fail("EXPORT_FAILED", "Preview folder does not exist and could not be created: " .. tostring(folder))
	end

	local session = LrExportSession({
		photosToExport = { photo },
		exportSettings = {
			LR_export_destinationType = "specificFolder",
			LR_export_destinationPathPrefix = folder,
			LR_export_useSubfolder = false,
			LR_reimportExportedPhoto = false,
			LR_collisionHandling = "rename",
			LR_renamingTokensOn = true,
			LR_tokens = "{{custom_token}}",
			LR_tokenCustomString = name,
			LR_extensionCase = "lowercase",
			LR_format = "JPEG",
			LR_export_colorSpace = "sRGB",
			LR_jpeg_quality = quality,
			LR_jpeg_useLimitSize = false,
			LR_size_doConstrain = true,
			LR_size_doNotEnlarge = true,
			LR_size_resizeType = "longEdge",
			LR_size_maxHeight = longEdge,
			LR_size_maxWidth = longEdge,
			LR_size_units = "pixels",
			LR_size_resolution = 72,
			LR_size_resolutionUnits = "inch",
			LR_outputSharpeningOn = false,
			LR_minimizeEmbeddedMetadata = true,
			LR_removeLocationMetadata = true,
			LR_useWatermark = false,
			LR_includeVideoFiles = false,
		},
	})

	local path
	for _, rendition in session:renditions() do
		local ok, pathOrMessage = rendition:waitForRender()
		if not ok then
			fail("EXPORT_FAILED", tostring(pathOrMessage))
		end
		path = pathOrMessage
	end
	if not path or LrFileUtils.exists(path) ~= "file" then
		fail("EXPORT_FAILED", "Lightroom reported no rendered file")
	end
	return { path = path, long_edge = longEdge }
end

return Commands
