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
local LrApplicationView = import "LrApplicationView"
local LrDevelopController = import "LrDevelopController"
local LrExportSession = import "LrExportSession"
local LrFileUtils = import "LrFileUtils"
local LrPathUtils = import "LrPathUtils"
local LrSelection = import "LrSelection"
local LrTasks = import "LrTasks"

local Json = require "Json"

local PLUGIN_VERSION = "0.12.0"
local PROTOCOL_VERSION = 1
local WRITE_ACCESS_TIMEOUT_S = 10
local MASK_WAIT_S = 25

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

local function normalizePath(path)
	if type(path) ~= "string" then
		return nil
	end
	local p = string.lower(string.gsub(path, "/", "\\"))
	return (string.gsub(p, "\\+$", ""))
end

-- Photo lookup for write commands, with two extra refusals:
-- * photos_root (optional, from bridge.json): the photo's file must be under that folder.
--   A virtual copy has the same file path as its master, so the rule covers copies too.
-- * needsVirtualCopy: develop edits are only made to virtual copies, never to master photos.
local function findWritablePhoto(ctx, id, needsVirtualCopy)
	local photo = findPhoto(ctx, id)
	local root = ctx.config.photos_root
	if type(root) == "string" and root ~= "" then
		local path = normalizePath(photo:getRawMetadata("path"))
		local prefix = normalizePath(root) .. "\\"
		if not path or string.sub(path, 1, #prefix) ~= prefix then
			fail("PHOTO_OUTSIDE_ROOT", "Refused: the photo's file is not under the allowed folder (" .. root .. ")")
		end
	end
	if needsVirtualCopy and not photo:getRawMetadata("isVirtualCopy") then
		fail("NOT_A_VIRTUAL_COPY", "Refused: develop edits are only made to virtual copies, never to master photos")
	end
	return photo
end

-- Stack state (read-only). getRawMetadata yields, so it must run inside LrTasks.pcall (a plain pcall
-- fails with "Yielding is not allowed within a C or metamethod call"). A photo inside a COLLAPSED stack cannot be shown in Develop, which
-- the mask tools need. Returns inStack, collapsed, note; the first two are nil when Lightroom does
-- not report them, and note then says why (the SDK error or the type it returned).
local function stackState(photo)
	local okIn, inStack = LrTasks.pcall(function()
		return photo:getRawMetadata("isInStackInFolder")
	end)
	if not okIn then
		return nil, nil, "isInStackInFolder raised: " .. tostring(inStack)
	end
	if type(inStack) ~= "boolean" then
		return nil, nil, "isInStackInFolder returned " .. type(inStack)
	end
	if not inStack then
		return false, false, nil
	end
	local okCollapsed, collapsed = LrTasks.pcall(function()
		return photo:getRawMetadata("stackInFolderIsCollapsed")
	end)
	if okCollapsed and type(collapsed) == "boolean" then
		return true, collapsed, nil
	end
	return true, nil, "stackInFolderIsCollapsed " .. (okCollapsed and ("returned " .. type(collapsed)) or ("raised: " .. tostring(collapsed)))
end

local function summary(photo)
	local inStack, stackCollapsed, stackNote = stackState(photo)
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
		in_stack = inStack,
		stack_collapsed = stackCollapsed,
		stack_note = stackNote,
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
		instance = ctx.instance,
		protocol = PROTOCOL_VERSION,
		lightroom_version = LrApplication.versionString(),
		catalog_path = ctx.catalog_path,
		configured_catalog = ctx.config.catalog_path,
		photos_root = ctx.config.photos_root,
		writes_allowed = ctx.writes_allowed,
	}
end

-- Lists the commands this running plug-in build has registered, so the client can check that
-- the build it expects is the one loaded in Lightroom.
function Commands.read.list_commands()
	local function names(group)
		local result = {}
		for name in pairs(group) do
			result[#result + 1] = name
		end
		table.sort(result)
		return list(result)
	end
	return { plugin_version = PLUGIN_VERSION, read = names(Commands.read), write = names(Commands.write) }
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

-- Facts about one photo from the catalog and its EXIF. Each key is read inside LrTasks.pcall (the
-- plain pcall cannot yield, and Lightroom may need to yield while it loads metadata, which made
-- every value come back empty). A key that fails gives nil and its error text is reported in
-- lookup_errors, so a missing value is visible and never guessed.
local function safeFormatted(photo, key, errors)
	local ok, value = LrTasks.pcall(function()
		return photo:getFormattedMetadata(key)
	end)
	if not ok then
		errors[key] = tostring(value)
		return nil
	end
	if value ~= nil and value ~= "" then
		return tostring(value)
	end
	return nil
end

local function safeRaw(photo, key, errors)
	local ok, value = LrTasks.pcall(function()
		return photo:getRawMetadata(key)
	end)
	if not ok then
		errors["raw:" .. key] = tostring(value)
		return nil
	end
	return value
end

local function metadata(photo)
	local info = summary(photo)
	local errors = {}
	local captured = safeRaw(photo, "captureTime", errors)
	local edits = safeRaw(photo, "editCount", errors)
	local result = {
		id = info.id,
		file_name = info.file_name,
		path = info.path,
		file_format = info.file_format,
		is_virtual_copy = info.is_virtual_copy,
		width = info.width,
		height = info.height,
		camera_make = safeFormatted(photo, "cameraMake", errors),
		camera_model = safeFormatted(photo, "cameraModel", errors),
		lens = safeFormatted(photo, "lens", errors),
		focal_length = safeFormatted(photo, "focalLength", errors),
		aperture = safeFormatted(photo, "aperture", errors),
		shutter_speed = safeFormatted(photo, "shutterSpeed", errors),
		iso = safeFormatted(photo, "isoSpeedRating", errors),
		exposure_bias = safeFormatted(photo, "exposureBias", errors),
		flash = safeFormatted(photo, "flash", errors),
		capture_time = safeFormatted(photo, "dateTimeOriginal", errors) or (captured and tostring(captured) or nil),
		rating = safeRaw(photo, "rating", errors),
		pick_status = safeRaw(photo, "pickStatus", errors),
		has_develop_edits = edits ~= nil and edits > 0 or nil,
	}
	if next(errors) ~= nil then
		result.lookup_errors = errors
	end
	return result
end

function Commands.read.get_metadata(params, ctx)
	return metadata(findPhoto(ctx, params.photo_id))
end

-- One page of the open catalog (master photos unless include_virtual_copies is true), in a stable order.
-- Optional list filters (all read-only): min_rating / max_rating (0-5, unrated counts as 0), flag
-- (pick | none | reject), keyword (a name on the photo, any letter case). There is no "edited" filter:
-- Lightroom counts every virtual copy as edited from the moment it is made, so it cannot tell touched from bare.
local FLAG_STATUS = { pick = 1, none = 0, reject = -1 }

local function listFilters(params)
	local filters = {}
	local names = {}
	for _, name in ipairs({ "min_rating", "max_rating" }) do
		local value = params[name]
		if value ~= nil then
			if type(value) ~= "number" or value < 0 or value > 5 or value % 1 ~= 0 then
				fail("INVALID_PARAMS", name .. " must be a whole number from 0 to 5")
			end
			filters[name] = value
			names[#names + 1] = name
		end
	end
	if filters.min_rating and filters.max_rating and filters.min_rating > filters.max_rating then
		fail("INVALID_PARAMS", "min_rating must not be greater than max_rating")
	end
	if params.flag ~= nil then
		if type(params.flag) ~= "string" or FLAG_STATUS[params.flag] == nil then
			fail("INVALID_PARAMS", "flag must be pick, none or reject")
		end
		filters.pick = FLAG_STATUS[params.flag]
		names[#names + 1] = "flag"
	end
	if params.keyword ~= nil then
		if type(params.keyword) ~= "string" or params.keyword == "" then
			fail("INVALID_PARAMS", "keyword must be a non-empty name")
		end
		filters.keyword = string.lower(params.keyword)
		names[#names + 1] = "keyword"
	end
	for _, name in ipairs({ "min_iso", "max_iso" }) do
		local value = params[name]
		if value ~= nil then
			if type(value) ~= "number" or value < 0 or value % 1 ~= 0 then
				fail("INVALID_PARAMS", name .. " must be a whole number, 0 or more")
			end
			filters[name] = value
			names[#names + 1] = name
		end
	end
	if filters.min_iso and filters.max_iso and filters.min_iso > filters.max_iso then
		fail("INVALID_PARAMS", "min_iso must not be greater than max_iso")
	end
	if params.file_format ~= nil then
		if type(params.file_format) ~= "string" or params.file_format == "" then
			fail("INVALID_PARAMS", "file_format must be a non-empty name such as RAW, DNG, JPG, TIFF, PSD or VIDEO")
		end
		filters.file_format = string.upper(params.file_format)
		names[#names + 1] = "file_format"
	end
	for _, name in ipairs({ "captured_from", "captured_to" }) do
		local value = params[name]
		if value ~= nil then
			local year, month, day = nil, nil, nil
			if type(value) == "string" then
				year, month, day = string.match(value, "^(%d%d%d%d)%-(%d%d)%-(%d%d)$")
			end
			if not year or tonumber(month) < 1 or tonumber(month) > 12 or tonumber(day) < 1 or tonumber(day) > 31 then
				fail("INVALID_PARAMS", name .. " must be a date written YYYY-MM-DD")
			end
			filters[name] = value
			names[#names + 1] = name
		end
	end
	if filters.captured_from and filters.captured_to and filters.captured_from > filters.captured_to then
		fail("INVALID_PARAMS", "captured_from must not be later than captured_to")
	end
	filters.names = names
	filters.active = #names > 0
	return filters
end

local function matchesFilters(photo, filters)
	if filters.min_rating or filters.max_rating then
		local rating = photo:getRawMetadata("rating") or 0
		if filters.min_rating and rating < filters.min_rating then
			return false
		end
		if filters.max_rating and rating > filters.max_rating then
			return false
		end
	end
	if filters.pick ~= nil and (photo:getRawMetadata("pickStatus") or 0) ~= filters.pick then
		return false
	end
	if filters.file_format then
		if string.upper(tostring(photo:getRawMetadata("fileFormat") or "")) ~= filters.file_format then
			return false
		end
	end
	if filters.min_iso or filters.max_iso then
		local iso = photo:getRawMetadata("isoSpeedRating")
		if type(iso) ~= "number" then
			return false -- no recorded ISO: it cannot be in a range
		end
		if filters.min_iso and iso < filters.min_iso then
			return false
		end
		if filters.max_iso and iso > filters.max_iso then
			return false
		end
	end
	if filters.captured_from or filters.captured_to then
		local stamp = photo:getRawMetadata("dateTimeOriginalISO8601")
		if type(stamp) ~= "string" or #stamp < 10 then
			return false -- no capture date: it cannot be in a range
		end
		local day = string.sub(stamp, 1, 10)
		if filters.captured_from and day < filters.captured_from then
			return false
		end
		if filters.captured_to and day > filters.captured_to then
			return false
		end
	end
	if filters.keyword then
		local found = false
		for _, keyword in ipairs(photo:getRawMetadata("keywords") or {}) do
			if string.lower(keyword:getName()) == filters.keyword then
				found = true
				break
			end
		end
		if not found then
			return false
		end
	end
	return true
end

function Commands.read.list_photos(params, ctx)
	local offset = tonumber(params.offset) or 0
	local limit = tonumber(params.limit) or 50
	if offset < 0 or limit < 1 or limit > 200 then
		fail("INVALID_PARAMS", "offset must be >= 0 and limit 1-200")
	end
	local wanted = nil
	if type(params.path_contains) == "string" and params.path_contains ~= "" then
		wanted = string.lower(params.path_contains)
	end
	local filters = listFilters(params)
	local matches = {}
	for _, photo in ipairs(ctx.catalog:getAllPhotos()) do
		local isCopy = photo:getRawMetadata("isVirtualCopy") and true or false
		if params.include_virtual_copies or not isCopy then
			local path = photo:getRawMetadata("path") or ""
			if not wanted or string.find(string.lower(path), wanted, 1, true) then
				if filters.active then
					ctx.checkpoint()
				end
				if not filters.active or matchesFilters(photo, filters) then
					matches[#matches + 1] = { path = path, photo = photo }
				end
			end
		end
	end
	table.sort(matches, function(a, b)
		return a.path < b.path
	end)
	local photos = {}
	for i = offset + 1, math.min(offset + limit, #matches) do
		ctx.checkpoint()
		photos[#photos + 1] = metadata(matches[i].photo)
	end
	return {
		total = #matches,
		offset = offset,
		returned = #photos,
		photos = list(photos),
		filters_applied = list(filters.names),
	}
end

-- Folder import. Photos are added "without moving": nothing is copied, moved or changed on disk.
local IMPORT_EXTENSIONS = {
	jpg = true, jpeg = true, tif = true, tiff = true, png = true, dng = true, arw = true, nef = true,
	cr2 = true, cr3 = true, raf = true, orf = true, rw2 = true, heic = true,
}
local SCAN_LIMIT = 2000

local function checkedFolder(params, ctx)
	local folder = params.folder
	if type(folder) ~= "string" or folder == "" then
		fail("INVALID_PARAMS", "folder is required")
	end
	if LrFileUtils.exists(folder) ~= "directory" then
		fail("FOLDER_NOT_FOUND", "No such folder: " .. folder)
	end
	local root = ctx.config.photos_root
	if type(root) == "string" and root ~= "" then
		local normalized, base = normalizePath(folder), normalizePath(root)
		if normalized ~= base and string.sub(normalized, 1, #base + 1) ~= base .. "\\" then
			fail("PHOTO_OUTSIDE_ROOT", "Refused: the folder is not under the allowed folder (" .. root .. ")")
		end
	end
	return folder
end

local function scanFolder(params, ctx)
	local folder = checkedFolder(params, ctx)
	local iterator = params.recursive and LrFileUtils.recursiveFiles(folder) or LrFileUtils.files(folder)
	local newFiles, existing, unsupported, seen = {}, 0, 0, 0
	for path in iterator do
		ctx.checkpoint()
		local extension = string.lower(LrPathUtils.extension(path) or "")
		if IMPORT_EXTENSIONS[extension] then
			seen = seen + 1
			if seen > SCAN_LIMIT then
				break
			end
			if ctx.catalog:findPhotoByPath(path) then
				existing = existing + 1
			else
				newFiles[#newFiles + 1] = path
			end
		else
			unsupported = unsupported + 1
		end
	end
	table.sort(newFiles)
	return { folder = folder, new_files = newFiles, already_in_catalog = existing, unsupported_files = unsupported, truncated = seen > SCAN_LIMIT }
end

function Commands.read.scan_folder(params, ctx)
	local result = scanFolder(params, ctx)
	result.new_count = #result.new_files
	local shown = {}
	for i = 1, math.min(#result.new_files, 50) do
		shown[i] = result.new_files[i]
	end
	result.new_files = list(shown)
	return result
end

function Commands.write.import_folder(params, ctx)
	local maxFiles = tonumber(params.max_files) or 200
	if maxFiles < 1 or maxFiles > 500 then
		fail("INVALID_PARAMS", "max_files must be 1-500")
	end
	local scan = scanFolder(params, ctx)
	local added, failed = 0, {}
	local todo = {}
	for i = 1, math.min(#scan.new_files, maxFiles) do
		todo[#todo + 1] = scan.new_files[i]
	end
	withWrite(ctx, "darkroom_ai: import folder", function()
		for _, path in ipairs(todo) do
			ctx.checkpoint()
			local ok, err = LrTasks.pcall(function()
				ctx.catalog:addPhoto(path)
			end)
			if ok then
				added = added + 1
			else
				failed[#failed + 1] = { path = path, error = tostring(err) }
			end
		end
	end)
	return {
		folder = scan.folder,
		added = added,
		failed = list(failed),
		already_in_catalog = scan.already_in_catalog,
		left_for_next_call = #scan.new_files - #todo,
	}
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

-- Develop presets known to Lightroom (name, uuid, folder). Read-only.
local function allPresets()
	local result = {}
	for _, folder in ipairs(LrApplication.developPresetFolders() or {}) do
		for _, preset in ipairs(folder:getDevelopPresets() or {}) do
			result[#result + 1] = { preset = preset, name = preset:getName(), uuid = preset:getUuid(), folder = folder:getName() }
		end
	end
	return result
end

function Commands.read.list_presets()
	local result = {}
	for _, item in ipairs(allPresets()) do
		result[#result + 1] = { name = item.name, uuid = item.uuid, folder = item.folder }
	end
	return { presets = list(result) }
end

-- The settings a develop preset WOULD apply (LrDevelopPreset:getSetting). Read-only: nothing
-- is applied to any photo. Used to check a preset against the limits before applying it.
function Commands.read.get_preset(params)
	if type(params.preset_uuid) ~= "string" or params.preset_uuid == "" then
		fail("INVALID_PARAMS", "preset_uuid is required")
	end
	for _, item in ipairs(allPresets()) do
		if item.uuid == params.preset_uuid then
			local ok, settings = pcall(function()
				return item.preset:getSetting()
			end)
			if not ok then
				fail("PRESET_UNREADABLE", "Lightroom could not read this preset: " .. tostring(settings))
			end
			return { name = item.name, uuid = item.uuid, folder = item.folder, settings = settings or {} }
		end
	end
	fail("PRESET_NOT_FOUND", "No develop preset with that id")
end

--------------------------------------------------------------------------------
-- Write commands (only run when the open catalog is the configured catalog)

-- catalog:createVirtualCopies works on the current selection only, so the selection is saved,
-- set to the one target photo, verified, and restored afterwards.
function Commands.write.create_virtual_copy(params, ctx)
	local catalog = ctx.catalog
	local photo = findWritablePhoto(ctx, params.photo_id, false)
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

	-- Selects only the target photo: plain call first, then inside a write gate (which of the
	-- two Lightroom needs is not documented). Returns the method that worked, or nil.
	local lastError
	local function selectTarget()
		local ok, err = LrTasks.pcall(function()
			catalog:setSelectedPhotos(photo, {})
		end)
		if ok and onlyTargetSelected(1.5) then
			return "direct"
		end
		lastError = err or lastError
		ok, err = LrTasks.pcall(function()
			withWrite(ctx, "darkroom_ai: select photo", function()
				catalog:setSelectedPhotos(photo, {})
			end)
		end)
		if ok and onlyTargetSelected(1.5) then
			return "write-gate"
		end
		lastError = (type(err) == "table" and err.message) or err or lastError
		return nil
	end

	-- Observed on Lightroom 15.2.1: a photo that is not in the grid's current source (folder,
	-- collection, "Previous Import", ...) cannot be selected. In that case the source is switched
	-- to "All Photographs" for the duration of the call and put back afterwards.
	local savedSources
	local function restoreSources()
		if savedSources then
			if #savedSources == 1 and type(savedSources[1]) == "string" then
				catalog:setActiveSources(savedSources[1])
			else
				catalog:setActiveSources(savedSources)
			end
			-- Switching the source changes the selection a moment later; wait before restoring it.
			LrTasks.sleep(1)
		end
	end
	local function restoreView()
		pcall(restoreSources)
		pcall(restoreSelection)
	end

	local method = selectTarget()
	if not method then
		savedSources = catalog:getActiveSources()
		catalog:setActiveSources(catalog.kAllPhotos)
		LrTasks.sleep(0.5)
		method = selectTarget()
	end
	if not method then
		restoreView()
		fail(
			"SELECTION_FAILED",
			"Could not select only the requested photo, even in All Photographs (a Library filter may hide it). "
				.. "No virtual copy was created. Details: target local id "
				.. tostring(targetId)
				.. ", active local id "
				.. tostring(nowActive and nowActive.localIdentifier)
				.. ", selected count "
				.. tostring(nowSelected and #nowSelected)
				.. ", last error "
				.. tostring(lastError)
		)
	end

	local ok, copies = LrTasks.pcall(function()
		return catalog:createVirtualCopies(params.copy_name)
	end)
	restoreView()
	if not ok then
		fail("VIRTUAL_COPY_FAILED", tostring(copies))
	end
	if type(copies) ~= "table" or #copies ~= 1 then
		fail("VIRTUAL_COPY_FAILED", "Expected exactly one virtual copy, got " .. tostring(copies and #copies))
	end
	local result = summary(copies[1])
	result.selection_method = method
	result.switched_source = savedSources ~= nil
	return result
end

function Commands.write.create_snapshot(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, false)
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
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	local target
	for _, snap in ipairs(snapshots(photo)) do
		if (params.snapshot_id and snap.id == params.snapshot_id) or (params.name and snap.name == params.name) then
			target = snap
		end
	end
	if not target then
		fail("SNAPSHOT_NOT_FOUND", "No matching snapshot on this photo")
	end
	-- The SDK reference does not say which snapshot id applyDevelopSnapshot expects or whether it
	-- needs a write gate, so both are selectable until the spike has settled it.
	local id = params.id_kind == "global" and target.global_id or target.id
	local function apply()
		photo:applyDevelopSnapshot(id)
	end
	if params.gate == false then
		apply()
	else
		withWrite(ctx, "darkroom_ai: apply snapshot", apply)
	end
	return { applied = target, id_kind = params.id_kind or "local", gate = params.gate ~= false }
end

function Commands.write.apply_settings(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
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

-- Applies a develop preset (by uuid) to a VIRTUAL COPY and reports every setting it changed,
-- with the values before and after, so the change can be checked against the limits and undone.
-- Masks. LrDevelopController's mask functions only work while the Develop module shows the photo, so
-- this selects the (virtual copy) photo, switches Lightroom to Develop and opens Masking. The
-- person sees Lightroom change module while this runs. The result is checked, not assumed: the
-- mask count before and after is compared (AI masks are created asynchronously and can fail).
local MASK_TYPES = { aiSelection = true, rangeMask = true, gradient = true, radialGradient = true }
local MASK_SUBTYPES = {
	aiSelection = { subject = true, sky = true, background = true, objects = true, people = true, landscape = true },
	rangeMask = { color = true, luminance = true, depth = true },
}

local function maskCount()
	local ok, masks = LrTasks.pcall(function()
		return LrDevelopController.getAllMasks()
	end)
	if ok and type(masks) == "table" then
		return #masks
	end
	return nil
end

local function plainCopy(value, depth)
	local kind = type(value)
	if kind == "string" or kind == "number" or kind == "boolean" then
		return value
	end
	if kind == "table" and depth > 0 then
		local out = {}
		for key, inner in pairs(value) do
			if type(key) == "string" or type(key) == "number" then
				out[key] = plainCopy(inner, depth - 1)
			end
		end
		return out
	end
	return nil
end

-- What Lightroom reports for the masks of the photo shown in Develop. The exact shape of each entry
-- is not documented, so it is passed through (scalars only, six levels deep) for the caller.
-- Three levels was too shallow: a mask's Tools list sits two tables down, so its contents were
-- cut off and showed as an empty table (found in the 2026-11-22 portrait run).
local function maskDump()
	local ok, masks = LrTasks.pcall(function()
		return LrDevelopController.getAllMasks()
	end)
	local selected = nil
	LrTasks.pcall(function()
		selected = LrDevelopController.getSelectedMask()
	end)
	return {
		masks = ok and plainCopy(masks, 6) or {},
		selected_mask = selected,
	}
end

local SHOW_ATTEMPTS = 4

-- Makes Lightroom's Develop module show `photo` and opens Masking. Lightroom can be slow to follow
-- a selection change (or the person may have clicked another photo), so this re-selects and checks
-- up to SHOW_ATTEMPTS times before giving up, and says which photo Lightroom is showing instead.
local function showInMasking(ctx, photo)
	local wanted = photo:getRawMetadata("uuid")
	local shownName = nil
	for attempt = 1, SHOW_ATTEMPTS do
		ctx.checkpoint()
		ctx.catalog:setSelectedPhotos(photo, {})
		LrApplicationView.switchToModule("develop")
		LrTasks.sleep(0.5 * attempt + 0.5)
		local target = ctx.catalog:getTargetPhoto()
		if target and target:getRawMetadata("uuid") == wanted then
			LrDevelopController.goToMasking()
			LrTasks.sleep(0.5)
			return
		end
		shownName = target and target:getFormattedMetadata("fileName") or "no photo"
	end
	fail(
		"MASK_PHOTO_NOT_SHOWN",
		"Lightroom's Develop module is showing "
			.. tostring(shownName)
			.. " instead of the requested photo after "
			.. SHOW_ATTEMPTS
			.. " tries. Do not click in Lightroom while a mask tool runs."
	)
end

function Commands.write.create_mask(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	local maskType, subtype = params.mask_type, params.subtype
	if not MASK_TYPES[maskType] then
		fail("INVALID_PARAMS", "mask_type must be aiSelection, rangeMask, gradient or radialGradient")
	end
	local allowed = MASK_SUBTYPES[maskType]
	if allowed and not allowed[subtype] then
		fail("INVALID_PARAMS", "subtype is not valid for mask_type " .. maskType)
	end
	if not allowed then
		subtype = nil
	end

	showInMasking(ctx, photo)

	local before = maskCount()
	local ok, err = LrTasks.pcall(function()
		LrDevelopController.createNewMask(maskType, subtype)
	end)
	if not ok then
		fail("MASK_CREATE_FAILED", "Lightroom refused to create the mask: " .. tostring(err))
	end
	local after = maskCount()
	local waited = 0
	while waited < MASK_WAIT_S and not (before and after and after > before) do
		ctx.checkpoint()
		LrTasks.sleep(0.5)
		waited = waited + 0.5
		after = maskCount()
	end
	return {
		created = (before ~= nil and after ~= nil and after > before) or false,
		masks_before = before,
		masks_after = after,
		waited_s = waited,
		mask_id = maskDump().selected_mask,
	}
end

function Commands.read.inspect_masks(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	showInMasking(ctx, photo)
	return maskDump()
end

-- Sets local adjustments inside one mask. Each entry of params.values is
-- { change, min, max, max_step }: the new value is the current one plus the change, the change is
-- limited to +-max_step and the result to min..max. Returns before/after so the caller can undo.
function Commands.write.set_mask_values(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	if type(params.values) ~= "table" or next(params.values) == nil then
		fail("INVALID_PARAMS", "values must be a non-empty object")
	end
	showInMasking(ctx, photo)
	if type(params.mask_id) == "string" and params.mask_id ~= "" then
		local ok, err = LrTasks.pcall(function()
			LrDevelopController.selectMask(params.mask_id)
		end)
		if not ok then
			fail("MASK_NOT_FOUND", "Lightroom could not select mask " .. params.mask_id .. ": " .. tostring(err))
		end
		LrTasks.sleep(0.3)
	end
	local selected = nil
	LrTasks.pcall(function()
		selected = LrDevelopController.getSelectedMask()
	end)
	if not selected then
		fail("MASK_NOT_FOUND", "No mask is selected in Lightroom")
	end
	local before, after = {}, {}
	for key, spec in pairs(params.values) do
		ctx.checkpoint()
		if type(key) ~= "string" or string.sub(key, 1, 6) ~= "local_" or type(spec) ~= "table" then
			fail("INVALID_PARAMS", "values keys must be local_* names with a spec object")
		end
		local current = LrDevelopController.getValue(key)
		if type(current) ~= "number" then
			fail("MASK_PARAM_UNAVAILABLE", key .. " is not available for the selected mask")
		end
		local step = spec.max_step or math.abs(spec.change)
		local change = math.max(-step, math.min(step, spec.change))
		local target = math.max(spec.min, math.min(spec.max, current + change))
		before[key] = current
		LrDevelopController.setValue(key, target)
		LrTasks.sleep(0.15)
		after[key] = LrDevelopController.getValue(key)
	end
	return { mask_id = selected, before = before, after = after }
end

-- Inverts or deletes one mask of the photo shown in Develop.
function Commands.write.mask_operation(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	local operation = params.operation
	if operation ~= "invert" and operation ~= "delete" then
		fail("INVALID_PARAMS", "operation must be invert or delete")
	end
	if type(params.mask_id) ~= "string" or params.mask_id == "" then
		fail("INVALID_PARAMS", "mask_id is required")
	end
	showInMasking(ctx, photo)
	local before = #(maskDump().masks)
	local ok, result = LrTasks.pcall(function()
		if operation == "invert" then
			return LrDevelopController.invertMask(params.mask_id)
		end
		return LrDevelopController.deleteMask(params.mask_id)
	end)
	if not ok then
		fail("MASK_OPERATION_FAILED", "Lightroom refused to " .. operation .. " the mask: " .. tostring(result))
	end
	LrTasks.sleep(0.5)
	return { operation = operation, masks_before = before, masks_after = #(maskDump().masks), accepted = result }
end

function Commands.write.apply_preset(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	if type(params.preset_uuid) ~= "string" or params.preset_uuid == "" then
		fail("INVALID_PARAMS", "preset_uuid is required")
	end
	local chosen
	for _, item in ipairs(allPresets()) do
		if item.uuid == params.preset_uuid then
			chosen = item
		end
	end
	if not chosen then
		fail("PRESET_NOT_FOUND", "No develop preset with that id")
	end

	local function comparable(value)
		if type(value) == "table" then
			local ok, text = pcall(Json.encode, value)
			return ok and text or tostring(value)
		end
		return value
	end

	local beforeAll = photo:getDevelopSettings()
	withWrite(ctx, "darkroom_ai: apply preset " .. chosen.name, function()
		photo:applyDevelopPreset(chosen.preset)
	end)
	local afterAll = photo:getDevelopSettings()

	local before, after = {}, {}
	for key, value in pairs(afterAll) do
		if comparable(value) ~= comparable(beforeAll[key]) then
			local previous = beforeAll[key]
			if previous == nil and type(value) == "table" then
				-- The photo had none of this (for example no masks): "before" is an empty list, so
				-- undoing the preset removes what it added instead of leaving it in place.
				previous = list({})
			end
			before[key] = previous
			after[key] = value
		end
	end
	return {
		preset = { name = chosen.name, uuid = chosen.uuid, folder = chosen.folder },
		before = before,
		after = after,
	}
end

-- Exports a JPEG preview into the configured preview folder only (never an arbitrary path).
-- Existing files are never overwritten: Lightroom renames on collision and the real path is returned.
function Commands.write.export_preview(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, false)
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

-- Exports a full-size 16-bit TIFF of a VIRTUAL COPY for the Photoshop hand-off.
-- * Write-gated like every command here: token + open catalog must be the configured catalog,
--   and the photo must be under photos_root when that is set.
-- * Writes only into handoff_dir from bridge.json (never a path from the request).
-- * Never overwrites: an existing target name is refused, and Lightroom's collision handling is
--   set to "rename" as a second line of defence. The real path is returned.
function Commands.write.export_tiff(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	local name = params.name
	if type(name) ~= "string" or not string.match(name, "^[%w_%-]+$") or #name > 80 then
		fail("INVALID_PARAMS", "name must be 1-80 characters of letters, digits, '_' or '-'")
	end
	local colorSpace = params.color_space or "sRGB"
	if colorSpace ~= "sRGB" and colorSpace ~= "AdobeRGB" and colorSpace ~= "ProPhotoRGB" then
		fail("INVALID_PARAMS", "color_space must be sRGB, AdobeRGB or ProPhotoRGB")
	end

	local folder = ctx.config.handoff_dir
	if type(folder) ~= "string" or folder == "" then
		fail("EXPORT_FAILED", "handoff_dir is not set in bridge.json (run `darkroom lightroom setup`)")
	end
	if LrFileUtils.exists(folder) ~= "directory" then
		LrFileUtils.createAllDirectories(folder)
	end
	if LrFileUtils.exists(folder) ~= "directory" then
		fail("EXPORT_FAILED", "Hand-off folder does not exist and could not be created: " .. folder)
	end
	local target = LrPathUtils.child(folder, name .. ".tif")
	if LrFileUtils.exists(target) then
		fail("FILE_EXISTS", "Refused: a file with this name already exists: " .. target)
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
			LR_format = "TIFF",
			LR_export_bitDepth = 16,
			LR_export_colorSpace = colorSpace,
			-- The SDK guide prints this key without the underscore; set both spellings.
			LR_tiff_compressionMethod = "compressionMethod_None",
			LRtiff_compressionMethod = "compressionMethod_None",
			LR_size_doConstrain = false,
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
	return { path = path, bit_depth = 16, color_space = colorSpace }
end

--------------------------------------------------------------------------------
-- Library state: star rating, pick/reject flag, keywords.
-- Catalog metadata only: no pixels, no develop settings. Writes go to virtual copies only (the
-- same rule as every other write), so a master photo and its file are never touched.

local MAX_KEYWORDS_PER_CALL = 20
local MAX_KEYWORD_LENGTH = 64

local function keywordNames(photo)
	local names = {}
	for _, keyword in ipairs(photo:getRawMetadata("keywords") or {}) do
		names[#names + 1] = keyword:getName()
	end
	table.sort(names)
	return names
end

local function libraryState(photo)
	return {
		rating = photo:getRawMetadata("rating"),
		pick_status = photo:getRawMetadata("pickStatus") or 0,
		keywords = list(keywordNames(photo)),
	}
end

-- A keyword anywhere in the catalog's keyword tree, found by name (case-insensitive), or nil.
local function findKeyword(keywords, wanted)
	for _, keyword in ipairs(keywords) do
		if string.lower(keyword:getName()) == wanted then
			return keyword
		end
		local found = findKeyword(keyword:getChildren() or {}, wanted)
		if found then
			return found
		end
	end
	return nil
end

local function keywordNameList(value, label)
	if value == nil then
		return {}
	end
	if type(value) ~= "table" or #value > MAX_KEYWORDS_PER_CALL then
		fail("INVALID_PARAMS", label .. " must be a list of at most " .. MAX_KEYWORDS_PER_CALL .. " names")
	end
	local names = {}
	for _, name in ipairs(value) do
		if type(name) ~= "string" then
			fail("INVALID_PARAMS", label .. " must contain only text")
		end
		local trimmed = string.match(name, "^%s*(.-)%s*$")
		if trimmed == "" or #trimmed > MAX_KEYWORD_LENGTH then
			fail("INVALID_PARAMS", "a keyword must be 1-" .. MAX_KEYWORD_LENGTH .. " characters")
		end
		names[#names + 1] = trimmed
	end
	return names
end

function Commands.read.get_library_state(params, ctx)
	local photo = findPhoto(ctx, params.photo_id)
	local state = libraryState(photo)
	state.id = photo:getRawMetadata("uuid")
	return state
end

function Commands.write.set_library_state(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	local rating = params.rating
	if rating ~= nil and (type(rating) ~= "number" or rating < 0 or rating > 5 or rating % 1 ~= 0) then
		fail("INVALID_PARAMS", "rating must be a whole number from 0 (no rating) to 5")
	end
	local pick = params.pick_status
	if pick ~= nil and pick ~= 1 and pick ~= 0 and pick ~= -1 then
		fail("INVALID_PARAMS", "pick_status must be 1 (pick), 0 (no flag) or -1 (reject)")
	end
	local add = keywordNameList(params.add_keywords, "add_keywords")
	local remove = keywordNameList(params.remove_keywords, "remove_keywords")
	if rating == nil and pick == nil and #add == 0 and #remove == 0 then
		fail("INVALID_PARAMS", "nothing to change: give rating, pick_status, add_keywords or remove_keywords")
	end

	local before = libraryState(photo)
	withWrite(ctx, "darkroom_ai: library state", function()
		if rating ~= nil then
			if rating == 0 then
				photo:setRawMetadata("rating", nil)
			else
				photo:setRawMetadata("rating", rating)
			end
		end
		if pick ~= nil then
			photo:setRawMetadata("pickStatus", pick)
		end
		for _, name in ipairs(add) do
			local wanted = string.lower(name)
			local keyword = findKeyword(ctx.catalog:getKeywords() or {}, wanted)
			if not keyword then
				keyword = ctx.catalog:createKeyword(name, {}, true, nil, false)
			end
			photo:addKeyword(keyword)
		end
		for _, name in ipairs(remove) do
			local wanted = string.lower(name)
			for _, keyword in ipairs(photo:getRawMetadata("keywords") or {}) do
				if string.lower(keyword:getName()) == wanted then
					photo:removeKeyword(keyword)
				end
			end
		end
	end)
	return { before = before, after = libraryState(photo) }
end

return Commands
