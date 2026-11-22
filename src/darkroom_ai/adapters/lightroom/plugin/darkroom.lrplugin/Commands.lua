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

local PLUGIN_VERSION = "0.15.0"
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

-- The folders photos may be edited or imported from: photos_root (the first one) plus photos_roots
-- (added later by the owner). No roots configured means no folder limit.
local function allowedRoots(ctx)
	local roots = {}
	local first = ctx.config.photos_root
	if type(first) == "string" and first ~= "" then
		roots[#roots + 1] = first
	end
	if type(ctx.config.photos_roots) == "table" then
		for _, root in ipairs(ctx.config.photos_roots) do
			if type(root) == "string" and root ~= "" then
				roots[#roots + 1] = root
			end
		end
	end
	return roots
end

local function underAllowedRoot(ctx, path)
	local roots = allowedRoots(ctx)
	if #roots == 0 then
		return true, nil
	end
	local normalized = normalizePath(path)
	for _, root in ipairs(roots) do
		local base = normalizePath(root)
		if normalized and (normalized == base or string.sub(normalized, 1, #base + 1) == base .. "\\") then
			return true, nil
		end
	end
	return false, table.concat(roots, "; ")
end

-- Photo lookup for write commands, with two extra refusals:
-- * allowed folders (optional, from bridge.json): the photo's file must be under one of them.
--   A virtual copy has the same file path as its master, so the rule covers copies too.
-- * needsVirtualCopy: develop edits are only made to virtual copies, never to master photos.
local function findWritablePhoto(ctx, id, needsVirtualCopy)
	local photo = findPhoto(ctx, id)
	local ok, shown = underAllowedRoot(ctx, photo:getRawMetadata("path"))
	if not ok then
		fail("PHOTO_OUTSIDE_ROOT", "Refused: the photo's file is not under an allowed folder (" .. shown .. ")")
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
		allowed_catalogs = list(ctx.config.allowed_catalogs or {}),
		photos_roots = list(allowedRoots(ctx)),
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

-- Value of a raw metadata key: from the batch row when Lightroom returned it, else asked of the photo.
local function rawValue(photo, row, key)
	local value = row and row[key]
	if value == nil then
		value = photo:getRawMetadata(key)
	end
	return value
end

local function matchesFilters(photo, filters, row)
	if filters.min_rating or filters.max_rating then
		local rating = rawValue(photo, row, "rating") or 0
		if filters.min_rating and rating < filters.min_rating then
			return false
		end
		if filters.max_rating and rating > filters.max_rating then
			return false
		end
	end
	if filters.pick ~= nil and (rawValue(photo, row, "pickStatus") or 0) ~= filters.pick then
		return false
	end
	if filters.file_format then
		if string.upper(tostring(rawValue(photo, row, "fileFormat") or "")) ~= filters.file_format then
			return false
		end
	end
	if filters.min_iso or filters.max_iso then
		local iso = rawValue(photo, row, "isoSpeedRating")
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
		local stamp = rawValue(photo, row, "dateTimeOriginalISO8601")
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
	local everyPhoto = ctx.catalog:getAllPhotos()
	-- One batch call for the values every photo needs: far faster than a call per photo and key
	-- (a large catalog otherwise takes longer than the client waits).
	local keys = { "path", "isVirtualCopy" }
	if filters.min_rating or filters.max_rating then
		keys[#keys + 1] = "rating"
	end
	if filters.pick ~= nil then
		keys[#keys + 1] = "pickStatus"
	end
	if filters.file_format then
		keys[#keys + 1] = "fileFormat"
	end
	if filters.min_iso or filters.max_iso then
		keys[#keys + 1] = "isoSpeedRating"
	end
	if filters.captured_from or filters.captured_to then
		keys[#keys + 1] = "dateTimeOriginalISO8601"
	end
	local rows = ctx.catalog:batchGetRawMetadata(everyPhoto, keys) or {}
	for index, photo in ipairs(everyPhoto) do
		if index % 100 == 0 then
			ctx.checkpoint()
		end
		local row = rows[photo]
		local isCopy = rawValue(photo, row, "isVirtualCopy") and true or false
		if params.include_virtual_copies or not isCopy then
			local path = rawValue(photo, row, "path") or ""
			if not wanted or string.find(string.lower(path), wanted, 1, true) then
				if not filters.active or matchesFilters(photo, filters, row) then
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
	local ok, shown = underAllowedRoot(ctx, folder)
	if not ok then
		fail("PHOTO_OUTSIDE_ROOT", "Refused: the folder is not under an allowed folder (" .. shown .. ")")
	end
	return folder
end

-- File names (lower case) of the photos already in the catalog, for the "same name elsewhere" hint.
-- Skipped for very large catalogs (returns nil) so a scan stays quick.
local NAME_INDEX_LIMIT = 5000

local function nameIndex(ctx)
	local photos = ctx.catalog:getAllPhotos()
	if #photos > NAME_INDEX_LIMIT then
		return nil
	end
	local names = {}
	local rows = ctx.catalog:batchGetFormattedMetadata(photos, { "fileName" }) or {}
	for _, photo in ipairs(photos) do
		local row = rows[photo]
		local name = row and row.fileName or photo:getFormattedMetadata("fileName")
		if type(name) == "string" then
			names[string.lower(name)] = true
		end
	end
	return names
end

local function scanFolder(params, ctx)
	local folder = checkedFolder(params, ctx)
	local iterator = params.recursive and LrFileUtils.recursiveFiles(folder) or LrFileUtils.files(folder)
	local newFiles, existing, unsupported, seen = {}, 0, 0, 0
	local names = nameIndex(ctx)
	local sameName = {}
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
				if names and names[string.lower(LrPathUtils.leafName(path))] then
					sameName[#sameName + 1] = path
				end
			end
		else
			unsupported = unsupported + 1
		end
	end
	table.sort(newFiles)
	return {
		folder = folder,
		new_files = newFiles,
		already_in_catalog = existing,
		unsupported_files = unsupported,
		truncated = seen > SCAN_LIMIT,
		same_name_files = sameName,
		same_name_checked = names ~= nil,
	}
end

function Commands.read.scan_folder(params, ctx)
	local result = scanFolder(params, ctx)
	result.new_count = #result.new_files
	local shown = {}
	for i = 1, math.min(#result.new_files, 50) do
		shown[i] = result.new_files[i]
	end
	result.new_files = list(shown)
	result.same_name_count = #result.same_name_files
	local sameShown = {}
	for i = 1, math.min(#result.same_name_files, 20) do
		sameShown[i] = result.same_name_files[i]
	end
	result.same_name_files = list(sameShown)
	return result
end

function Commands.write.import_folder(params, ctx)
	local maxFiles = tonumber(params.max_files) or 200
	if maxFiles < 1 or maxFiles > 500 then
		fail("INVALID_PARAMS", "max_files must be 1-500")
	end
	local scan = scanFolder(params, ctx)
	local added, failed, addedPaths = 0, {}, {}
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
				addedPaths[#addedPaths + 1] = path
			else
				failed[#failed + 1] = { path = path, error = tostring(err) }
			end
		end
	end)
	return {
		folder = scan.folder,
		added = added,
		added_paths = list(addedPaths),
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

-- Makes Lightroom's Develop module show `photo` (no tool opened). Same re-select-and-check loop as
-- showInMasking: Lightroom can be slow to follow, and the person may have clicked another photo.
local function showInDevelop(ctx, photo)
	local wanted = photo:getRawMetadata("uuid")
	local shownName = nil
	for attempt = 1, SHOW_ATTEMPTS do
		ctx.checkpoint()
		ctx.catalog:setSelectedPhotos(photo, {})
		LrApplicationView.switchToModule("develop")
		LrTasks.sleep(0.5 * attempt + 0.5)
		local target = ctx.catalog:getTargetPhoto()
		if target and target:getRawMetadata("uuid") == wanted then
			return
		end
		shownName = target and target:getFormattedMetadata("fileName") or "no photo"
	end
	fail(
		"DEVELOP_PHOTO_NOT_SHOWN",
		"Lightroom's Develop module is showing "
			.. tostring(shownName)
			.. " instead of the requested photo after "
			.. SHOW_ATTEMPTS
			.. " tries. Do not click in Lightroom while Auto runs."
	)
end

local AUTO_POLLS = 14

-- Plain (number, string, boolean) settings as a table, so two reads can be compared.
local function plainSettings(photo)
	local out = {}
	for key, value in pairs(photo:getDevelopSettings()) do
		local kind = type(value)
		if kind == "number" or kind == "string" or kind == "boolean" then
			out[key] = value
		end
	end
	return out
end

local function sameSettings(a, b)
	for key, value in pairs(a) do
		if b[key] ~= value then
			return false
		end
	end
	for key, value in pairs(b) do
		if a[key] ~= value then
			return false
		end
	end
	return true
end

-- Lightroom applies Develop-controller actions a moment later. Waits until the plain settings have
-- changed and then stopped changing (two equal reads in a row), or until `idlePolls` reads show no
-- change at all (nothing to do), and returns the settings as they are then.
local function settle(ctx, photo, beforeAll, idlePolls, maxPolls)
	local last = beforeAll
	local changed, stable = false, 0
	for poll = 1, maxPolls do
		ctx.checkpoint()
		LrTasks.sleep(0.5)
		local now = plainSettings(photo)
		if not sameSettings(now, beforeAll) then
			changed = true
		end
		if changed and sameSettings(now, last) then
			stable = stable + 1
			if stable >= 2 then
				return now
			end
		else
			stable = 0
		end
		last = now
		if not changed and poll >= idlePolls then
			return now
		end
	end
	return plainSettings(photo)
end

local function changedKeys(beforeAll, afterAll)
	local before, after = {}, {}
	for key, value in pairs(afterAll) do
		if beforeAll[key] ~= value then
			before[key] = beforeAll[key]
			after[key] = value
		end
	end
	return before, after
end

-- Lightroom's own Auto buttons of the Basic panel (Auto Tone, Auto White Balance), run on ONE photo:
-- Lightroom measures that photo and sets its sliders, so every photo gets values of its own.
-- Develop edits go to virtual copies only. Lightroom applies the result a moment later, so this
-- waits until the settings have stopped changing, then reports every plain setting that changed.
function Commands.write.auto_adjust(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	local wantTone = params.tone ~= false
	local wantWhiteBalance = params.white_balance == true
	if not wantTone and not wantWhiteBalance then
		fail("INVALID_PARAMS", "ask for tone, white_balance or both")
	end
	showInDevelop(ctx, photo)

	local beforeAll = plainSettings(photo)
	local ran = {}
	local function run(name, call)
		local ok, err = LrTasks.pcall(call)
		if not ok then
			fail("AUTO_FAILED", name .. " failed: " .. tostring(err))
		end
		ran[#ran + 1] = name
	end
	if wantTone then
		run("setAutoTone", function()
			LrDevelopController.setAutoTone()
		end)
	end
	if wantWhiteBalance then
		run("setAutoWhiteBalance", function()
			LrDevelopController.setAutoWhiteBalance()
		end)
	end

	local afterAll = settle(ctx, photo, beforeAll, 6, AUTO_POLLS)
	local before, after = changedKeys(beforeAll, afterAll)
	return { photo_id = photo:getRawMetadata("uuid"), ran = ran, before = before, after = after }
end

-- Takes a snapshot (a named save point) of the photo before a bigger change; returns it or nil.
local function savePointBefore(ctx, photo, label)
	local name = "darkroom_ai before " .. label .. " " .. os.date("%H:%M:%S")
	local ok, made = LrTasks.pcall(function()
		return Commands.write.create_snapshot({ photo_id = photo:getRawMetadata("uuid"), name = name }, ctx)
	end)
	if ok and type(made) == "table" then
		return made.snapshot
	end
	return nil
end

local RESET_SCOPES = {
	all = function()
		LrDevelopController.resetAllDevelopAdjustments()
	end,
	masks = function()
		LrDevelopController.resetMasking()
	end,
	spots = function()
		LrDevelopController.resetSpotRemoval()
	end,
	crop = function()
		LrDevelopController.resetCrop()
	end,
	transforms = function()
		LrDevelopController.resetTransforms()
	end,
	red_eye = function()
		LrDevelopController.resetRedeye()
	end,
}

-- Resets develop adjustments of a virtual copy, all of them or one group. A snapshot of the photo is
-- taken first, and every plain setting that changed is returned with its earlier value.
function Commands.write.reset_develop(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	local scope = params.scope
	local action = RESET_SCOPES[scope]
	if not action then
		fail("INVALID_PARAMS", "scope must be all, masks, spots, crop, transforms or red_eye")
	end
	local snapshot = savePointBefore(ctx, photo, "reset " .. scope)
	showInDevelop(ctx, photo)
	local beforeAll = plainSettings(photo)
	local ok, err = LrTasks.pcall(action)
	if not ok then
		fail("RESET_FAILED", "Lightroom refused the reset: " .. tostring(err))
	end
	local afterAll = settle(ctx, photo, beforeAll, 6, AUTO_POLLS)
	local before, after = changedKeys(beforeAll, afterAll)
	return { photo_id = photo:getRawMetadata("uuid"), scope = scope, snapshot = snapshot, before = before, after = after }
end

local function retouchCount(photo)
	local okCount, counted = LrTasks.pcall(function()
		return LrDevelopController.countAllSpots("distractingPeopleRemoval")
	end)
	if okCount and type(counted) == "number" then
		return counted
	end
	local ok, settings = LrTasks.pcall(function()
		return photo:getDevelopSettings()
	end)
	if not ok or type(settings) ~= "table" then
		return nil
	end
	local areas = settings.RetouchAreas
	return type(areas) == "table" and #areas or 0
end

-- Lightroom's own "distracting people" detection and removal (Remove tool). UNPROBED: the SDK says
-- little about timing, so this waits for the callback or for the spot list to change and reports
-- what it saw. A snapshot is taken first.
function Commands.write.remove_distracting_people(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	local snapshot = savePointBefore(ctx, photo, "remove people")
	showInDevelop(ctx, photo)
	local countBefore = retouchCount(photo)
	local ok, err = LrTasks.pcall(function()
		LrDevelopController.detectDistractingPeople()
	end)
	if not ok then
		fail("DETECT_FAILED", "Lightroom could not detect distracting people: " .. tostring(err))
	end
	LrTasks.sleep(3)
	local finished = false
	local applied, applyErr = LrTasks.pcall(function()
		LrDevelopController.applyRemovalOnDetectedDistractingPeople(function()
			finished = true
		end, {})
	end)
	if not applied then
		fail("REMOVE_FAILED", "Lightroom could not remove the detected people: " .. tostring(applyErr))
	end
	local waited = 0
	while waited < 60 do
		ctx.checkpoint()
		LrTasks.sleep(0.5)
		waited = waited + 0.5
		local now = retouchCount(photo)
		if finished or (countBefore ~= nil and now ~= nil and now ~= countBefore) then
			break
		end
	end
	LrTasks.sleep(1)
	return {
		photo_id = photo:getRawMetadata("uuid"),
		snapshot = snapshot,
		callback_called = finished,
		retouch_before = countBefore,
		retouch_after = retouchCount(photo),
		waited_s = waited,
	}
end

-- Reads the spot-removal / red-eye state from the photo's develop settings (no Develop view needed).
function Commands.read.get_retouch_state(params, ctx)
	local photo = findPhoto(ctx, params.photo_id)
	local settings = photo:getDevelopSettings()
	local areas = settings.RetouchAreas
	local redEye = settings.RedEyeInfo
	return {
		photo_id = photo:getRawMetadata("uuid"),
		spot_count = type(areas) == "table" and #areas or 0,
		spots = plainCopy(areas, 4) or Json.EMPTY_ARRAY,
		red_eye_count = type(redEye) == "table" and #redEye or 0,
		red_eye = plainCopy(redEye, 4) or Json.EMPTY_ARRAY,
	}
end

-- Raw answers of Lightroom's AI-panel getters for the photo shown in Develop, to learn what this
-- Lightroom version offers (Enhance, Reflection Removal, Remove panel, spots, current tool).
function Commands.read.get_ai_panel_state(params, ctx)
	local photo = findPhoto(ctx, params.photo_id)
	showInDevelop(ctx, photo)
	local probes = {
		enhance = function()
			return LrDevelopController.getEnhancePanelState()
		end,
		reflection_removal = function()
			return LrDevelopController.getReflectionRemovalPanelState()
		end,
		remove_panel = function()
			return LrDevelopController.getRemovePanelPreferences()
		end,
		selected_tool = function()
			return LrDevelopController.getSelectedTool()
		end,
		process_version = function()
			return LrDevelopController.getProcessVersion()
		end,
	}
	local out = {}
	for name, probe in pairs(probes) do
		local ok, value = LrTasks.pcall(probe)
		out[name] = { ok = ok and true or false, value = ok and plainCopy(value, 4) or tostring(value) }
	end
	return { photo_id = photo:getRawMetadata("uuid"), panels = out }
end

function Commands.write.rotate_photo(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	if params.direction ~= "left" and params.direction ~= "right" then
		fail("INVALID_PARAMS", "direction must be left or right")
	end
	local function orientation()
		local ok, value = LrTasks.pcall(function()
			return photo:getFormattedMetadata("orientation")
		end)
		return ok and value or nil
	end
	local before = orientation()
	withWrite(ctx, "darkroom_ai: rotate", function()
		if params.direction == "left" then
			photo:rotateLeft()
		else
			photo:rotateRight()
		end
	end)
	return { photo_id = photo:getRawMetadata("uuid"), direction = params.direction, before = before, after = orientation() }
end

local METADATA_FIELDS = { title = true, caption = true, headline = true, copyright = true, creator = true }

-- Descriptive metadata of a photo (catalog data only): title, caption, headline, copyright, creator,
-- and the colour label by colour name. Returns the earlier values.
function Commands.write.set_photo_metadata(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, false)
	local changes, color = {}, params.color_label
	for key, value in pairs(params) do
		if METADATA_FIELDS[key] then
			if type(value) ~= "string" or #value > 2000 then
				fail("INVALID_PARAMS", key .. " must be text of at most 2000 characters")
			end
			changes[key] = value
		end
	end
	if color ~= nil and color ~= "none" and not (color == "red" or color == "yellow" or color == "green" or color == "blue" or color == "purple") then
		fail("INVALID_PARAMS", "color_label must be red, yellow, green, blue, purple or none")
	end
	if next(changes) == nil and color == nil then
		fail("INVALID_PARAMS", "nothing to change: give title, caption, headline, copyright, creator or color_label")
	end
	local function snapshotValues()
		local out = {}
		for key in pairs(METADATA_FIELDS) do
			out[key] = photo:getFormattedMetadata(key) or ""
		end
		out.color_label = photo:getRawMetadata("colorNameForLabel") or "none"
		return out
	end
	local before = snapshotValues()
	withWrite(ctx, "darkroom_ai: metadata", function()
		for key, value in pairs(changes) do
			photo:setRawMetadata(key, value)
		end
		if color ~= nil then
			local labelName = nil
			if color ~= "none" then
				for label, name in pairs(ctx.catalog:getLabelMapToColorName() or {}) do
					if name == color then
						labelName = label
					end
				end
				if not labelName then
					fail("LABEL_NOT_DEFINED", "This catalog's label set has no " .. color .. " label")
				end
			end
			photo:setRawMetadata("label", labelName or "")
		end
	end)
	return { photo_id = photo:getRawMetadata("uuid"), before = before, after = snapshotValues() }
end

local MAX_KEYWORDS = 500

local function walkKeywords(keywords, prefix, out)
	for _, keyword in ipairs(keywords or {}) do
		if #out >= MAX_KEYWORDS then
			return true
		end
		local path = prefix == "" and keyword:getName() or (prefix .. "/" .. keyword:getName())
		out[#out + 1] = { path = path, synonyms = list(keyword:getSynonyms() or {}) }
		if walkKeywords(keyword:getChildren(), path, out) then
			return true
		end
	end
	return false
end

function Commands.read.list_keywords(_, ctx)
	local out = {}
	local truncated = walkKeywords(ctx.catalog:getKeywords(), "", out)
	return { keywords = list(out), truncated = truncated }
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
-- Catalog metadata only: no pixels, no develop settings. Allowed on master photos and on virtual copies
-- (owner decision 2026-11-22); the photo's file is never touched.

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
	local photo = findWritablePhoto(ctx, params.photo_id, false)
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

--------------------------------------------------------------------------------
-- Catalog organisation: collections, collection sets, keywords, catalog overview.
-- Catalog data only. Nothing here touches a photo's pixels, develop settings or file.

local MAX_PHOTOS_PER_CALL = 200
local MAX_TREE_NODES = 300

local function splitPath(text, label)
	if type(text) ~= "string" then
		fail("INVALID_PARAMS", label .. " must be text such as \"Trips/Japan 2026\"")
	end
	local parts = {}
	for part in string.gmatch(text, "[^/]+") do
		local trimmed = string.match(part, "^%s*(.-)%s*$")
		if trimmed ~= "" then
			if #trimmed > 100 then
				fail("INVALID_PARAMS", label .. ": each name must be at most 100 characters")
			end
			parts[#parts + 1] = trimmed
		end
	end
	if #parts == 0 then
		fail("INVALID_PARAMS", label .. " is empty")
	end
	return parts
end

local function childSets(ctx, parent)
	if parent then
		return parent:getChildCollectionSets() or {}
	end
	return ctx.catalog:getChildCollectionSets() or {}
end

local function childCollections(ctx, parent)
	if parent then
		return parent:getChildCollections() or {}
	end
	return ctx.catalog:getChildCollections() or {}
end

local function byName(items, name)
	local wanted = string.lower(name)
	for _, item in ipairs(items) do
		if string.lower(item:getName()) == wanted then
			return item
		end
	end
	return nil
end

-- The collection set at "A/B" (nil path = the top level). Returns set, or nil when it does not exist.
local function findSet(ctx, parts)
	local parent = nil
	for _, name in ipairs(parts) do
		local found = byName(childSets(ctx, parent), name)
		if not found then
			return nil, false
		end
		parent = found
	end
	return parent, true
end

local function findCollection(ctx, text)
	local parts = splitPath(text, "collection")
	local name = table.remove(parts)
	local parent, ok = findSet(ctx, parts)
	if not ok then
		return nil
	end
	return byName(childCollections(ctx, parent), name)
end

local function pathOf(item)
	local names = { item:getName() }
	local parent = item:getParent()
	while parent do
		table.insert(names, 1, parent:getName())
		parent = parent:getParent()
	end
	return table.concat(names, "/")
end

local function photoIds(photos)
	local ids = {}
	for _, photo in ipairs(photos) do
		ids[photo:getRawMetadata("uuid")] = true
	end
	return ids
end

local function idList(params, label)
	local ids = params.photo_ids
	if type(ids) ~= "table" or #ids == 0 or #ids > MAX_PHOTOS_PER_CALL then
		fail("INVALID_PARAMS", label .. " must be a list of 1-" .. MAX_PHOTOS_PER_CALL .. " photo ids")
	end
	for _, id in ipairs(ids) do
		if type(id) ~= "string" or id == "" then
			fail("INVALID_PARAMS", label .. " must contain only photo id text")
		end
	end
	return ids
end

function Commands.write.create_collection_set(params, ctx)
	local parts = splitPath(params.path, "path")
	local name = table.remove(parts)
	local parent, ok = findSet(ctx, parts)
	if not ok then
		fail("COLLECTION_NOT_FOUND", "The parent set does not exist: " .. table.concat(parts, "/"))
	end
	local existing = byName(childSets(ctx, parent), name)
	if not existing then
		withWrite(ctx, "darkroom_ai: create collection set", function()
			ctx.catalog:createCollectionSet(name, parent, true)
		end)
	end
	local now = byName(childSets(ctx, parent), name)
	if not now then
		fail("CREATE_FAILED", "Lightroom did not create the collection set " .. name)
	end
	return { path = pathOf(now), created = existing == nil }
end

function Commands.write.create_collection(params, ctx)
	local parts = splitPath(params.path, "path")
	local name = table.remove(parts)
	local parent, ok = findSet(ctx, parts)
	if not ok then
		fail("COLLECTION_NOT_FOUND", "The parent set does not exist: " .. table.concat(parts, "/") .. " (create it first)")
	end
	local existing = byName(childCollections(ctx, parent), name)
	if not existing then
		withWrite(ctx, "darkroom_ai: create collection", function()
			ctx.catalog:createCollection(name, parent, true)
		end)
	end
	local now = byName(childCollections(ctx, parent), name)
	if not now then
		fail("CREATE_FAILED", "Lightroom did not create the collection " .. name)
	end
	return { path = pathOf(now), created = existing == nil, photo_count = #(now:getPhotos() or {}) }
end

function Commands.write.add_to_collection(params, ctx)
	local ids = idList(params, "photo_ids")
	local collection = findCollection(ctx, params.collection)
	if not collection then
		fail("COLLECTION_NOT_FOUND", "No collection " .. tostring(params.collection) .. " (create_collection first)")
	end
	if collection:isSmartCollection() then
		fail("SMART_COLLECTION", "Smart collections fill themselves; photos cannot be added by hand")
	end
	local present = photoIds(collection:getPhotos() or {})
	local toAdd, addedIds, already, missing = {}, {}, 0, {}
	for _, id in ipairs(ids) do
		ctx.checkpoint()
		local photo = ctx.catalog:findPhotoByUuid(id)
		if not photo then
			missing[#missing + 1] = id
		else
			local ok, shown = underAllowedRoot(ctx, photo:getRawMetadata("path"))
			if not ok then
				fail("PHOTO_OUTSIDE_ROOT", "Refused: " .. id .. " is not under an allowed folder (" .. shown .. ")")
			end
			if present[id] then
				already = already + 1
			elseif not photoIds(toAdd)[id] then
				toAdd[#toAdd + 1] = photo
				addedIds[#addedIds + 1] = id
			end
		end
	end
	if #toAdd > 0 then
		withWrite(ctx, "darkroom_ai: add to collection", function()
			collection:addPhotos(toAdd)
		end)
	end
	return {
		collection = pathOf(collection),
		added_ids = list(addedIds),
		already_in = already,
		missing_ids = list(missing),
		photo_count = #(collection:getPhotos() or {}),
	}
end

function Commands.write.remove_from_collection(params, ctx)
	local ids = idList(params, "photo_ids")
	local collection = findCollection(ctx, params.collection)
	if not collection then
		fail("COLLECTION_NOT_FOUND", "No collection " .. tostring(params.collection))
	end
	if collection:isSmartCollection() then
		fail("SMART_COLLECTION", "Smart collections fill themselves; photos cannot be removed by hand")
	end
	local present = photoIds(collection:getPhotos() or {})
	local toRemove, removedIds = {}, {}
	for _, id in ipairs(ids) do
		ctx.checkpoint()
		local photo = ctx.catalog:findPhotoByUuid(id)
		if photo and present[id] then
			toRemove[#toRemove + 1] = photo
			removedIds[#removedIds + 1] = id
		end
	end
	if #toRemove > 0 then
		withWrite(ctx, "darkroom_ai: remove from collection", function()
			collection:removePhotos(toRemove)
		end)
	end
	return {
		collection = pathOf(collection),
		removed_ids = list(removedIds),
		photo_count = #(collection:getPhotos() or {}),
	}
end

-- Deletes an EMPTY collection or collection set (used to take back one darkroom_ai created).
function Commands.write.delete_collection(params, ctx)
	local parts = splitPath(params.path, "path")
	local kind = params.kind
	if kind ~= "collection" and kind ~= "set" then
		fail("INVALID_PARAMS", "kind must be 'collection' or 'set'")
	end
	local target
	if kind == "collection" then
		target = findCollection(ctx, params.path)
		if target and #(target:getPhotos() or {}) > 0 then
			fail("NOT_EMPTY", "Refused: the collection still has photos; remove them first")
		end
	else
		target = findSet(ctx, parts)
		if target and (#childSets(ctx, target) > 0 or #childCollections(ctx, target) > 0) then
			fail("NOT_EMPTY", "Refused: the set still contains collections or sets")
		end
	end
	if not target then
		return { deleted = false, path = params.path }
	end
	withWrite(ctx, "darkroom_ai: delete empty " .. kind, function()
		target:delete()
	end)
	return { deleted = true, path = params.path }
end

function Commands.read.collection_photos(params, ctx)
	local collection = findCollection(ctx, params.collection)
	if not collection then
		fail("COLLECTION_NOT_FOUND", "No collection " .. tostring(params.collection))
	end
	local limit = tonumber(params.limit) or 100
	if limit < 1 or limit > MAX_PHOTOS_PER_CALL then
		fail("INVALID_PARAMS", "limit must be 1-" .. MAX_PHOTOS_PER_CALL)
	end
	local photos = collection:getPhotos() or {}
	local shown = {}
	for i = 1, math.min(#photos, limit) do
		ctx.checkpoint()
		shown[i] = { id = photos[i]:getRawMetadata("uuid"), file_name = photos[i]:getFormattedMetadata("fileName") }
	end
	return {
		collection = pathOf(collection),
		is_smart = collection:isSmartCollection() and true or false,
		total = #photos,
		photos = list(shown),
	}
end

-- One level per write step: Lightroom does not let a keyword created in the same step be used as a
-- parent (the second level of "A/B" failed that way), so each level is committed before the next.
function Commands.write.create_keyword(params, ctx)
	local parts = splitPath(params.path, "path")
	local created, parent = {}, nil
	for _, name in ipairs(parts) do
		ctx.checkpoint()
		local siblings = parent and (parent:getChildren() or {}) or (ctx.catalog:getKeywords() or {})
		local found = byName(siblings, name)
		if not found then
			local level = parent
			withWrite(ctx, "darkroom_ai: create keyword", function()
				ctx.catalog:createKeyword(name, {}, true, level, true)
			end)
			siblings = level and (level:getChildren() or {}) or (ctx.catalog:getKeywords() or {})
			found = byName(siblings, name)
			if not found then
				fail("CREATE_FAILED", "Lightroom did not create the keyword " .. name)
			end
			created[#created + 1] = name
		end
		parent = found
	end
	return { path = table.concat(parts, "/"), created_levels = list(created) }
end

local function collectionTree(ctx, parent, depth, budget)
	local nodes = {}
	local function add(item, kind, children)
		if budget.left <= 0 then
			budget.truncated = true
			return
		end
		budget.left = budget.left - 1
		local node = { name = item:getName(), kind = kind }
		if kind == "collection" then
			node.photo_count = #(item:getPhotos() or {})
			node.is_smart = item:isSmartCollection() and true or false
		elseif depth < 4 then
			node.children = children
		end
		nodes[#nodes + 1] = node
	end
	for _, set in ipairs(childSets(ctx, parent)) do
		ctx.checkpoint()
		local children = depth < 4 and collectionTree(ctx, set, depth + 1, budget) or Json.EMPTY_ARRAY
		add(set, "set", children)
	end
	for _, collection in ipairs(childCollections(ctx, parent)) do
		ctx.checkpoint()
		add(collection, "collection")
	end
	return list(nodes)
end

local function folderTree(ctx, folders, depth, budget)
	local nodes = {}
	for _, folder in ipairs(folders) do
		ctx.checkpoint()
		if budget.left <= 0 then
			budget.truncated = true
			break
		end
		budget.left = budget.left - 1
		local node = { name = folder:getName(), path = folder:getPath(), photo_count = #(folder:getPhotos(false) or {}) }
		if depth < 4 then
			node.children = folderTree(ctx, folder:getChildren() or {}, depth + 1, budget)
		end
		nodes[#nodes + 1] = node
	end
	return list(nodes)
end

local function keywordCount(keywords, budget)
	for _, keyword in ipairs(keywords) do
		budget.count = budget.count + 1
		if budget.count > 5000 then
			return
		end
		keywordCount(keyword:getChildren() or {}, budget)
	end
end

function Commands.read.catalog_info(_, ctx)
	local photos = ctx.catalog:getAllPhotos()
	local copies = 0
	local rows = ctx.catalog:batchGetRawMetadata(photos, { "isVirtualCopy" }) or {}
	for _, photo in ipairs(photos) do
		local row = rows[photo]
		if rawValue(photo, row, "isVirtualCopy") then
			copies = copies + 1
		end
	end
	local collectionBudget = { left = MAX_TREE_NODES, truncated = false }
	local folderBudget = { left = MAX_TREE_NODES, truncated = false }
	local keywordBudget = { count = 0 }
	keywordCount(ctx.catalog:getKeywords() or {}, keywordBudget)
	return {
		catalog_path = ctx.catalog_path,
		writes_allowed = ctx.writes_allowed,
		total_photos = #photos,
		virtual_copies = copies,
		keyword_count = keywordBudget.count,
		collections = collectionTree(ctx, nil, 1, collectionBudget),
		folders = folderTree(ctx, ctx.catalog:getFolders() or {}, 1, folderBudget),
		truncated = collectionBudget.truncated or folderBudget.truncated,
	}
end


-- Smart searches (the same engine as Smart Collections). Only a fixed list of criteria and
-- operations is accepted, so a search can only READ the catalog. Names follow Lightroom's own
-- Smart Collection settings; UNVERIFIED on this Lightroom version.
local SEARCH_CRITERIA = {
	rating = { ops = { ["=="] = true, [">"] = true, [">="] = true, ["<"] = true, ["<="] = true }, kind = "number" },
	pick = { ops = { ["=="] = true }, kind = "number" },
	labelColor = { ops = { ["=="] = true }, kind = "string" },
	keywords = { ops = { any = true, all = true, noneOf = true }, kind = "string" },
	fileFormat = { ops = { ["=="] = true }, kind = "string" },
	iso = { ops = { ["=="] = true, [">"] = true, [">="] = true, ["<"] = true, ["<="] = true }, kind = "number" },
	captureTime = { ops = { [">"] = true, [">="] = true, ["<"] = true, ["<="] = true, ["=="] = true }, kind = "string" },
}

local function buildSearchDesc(params)
	local conditions = params.conditions
	if type(conditions) ~= "table" or #conditions == 0 or #conditions > 8 then
		fail("INVALID_PARAMS", "conditions must be a list of 1 to 8 conditions")
	end
	local combine = params.combine or "intersect"
	if combine ~= "intersect" and combine ~= "union" then
		fail("INVALID_PARAMS", "combine must be intersect or union")
	end
	local desc = { combine = combine }
	for index, condition in ipairs(conditions) do
		local rule = type(condition) == "table" and SEARCH_CRITERIA[condition.criteria] or nil
		if not rule then
			fail("INVALID_PARAMS", "condition " .. index .. ": criteria is not one that can be searched")
		end
		if not rule.ops[condition.operation] then
			fail("INVALID_PARAMS", "condition " .. index .. ": operation is not valid for " .. condition.criteria)
		end
		local value = condition.value
		if type(value) ~= rule.kind or (rule.kind == "string" and (value == "" or #value > 200)) then
			fail("INVALID_PARAMS", "condition " .. index .. ": value must be a " .. rule.kind)
		end
		desc[#desc + 1] = { criteria = condition.criteria, operation = condition.operation, value = value }
	end
	return desc
end

local MAX_FOUND = 200

function Commands.read.find_photos(params, ctx)
	local desc = buildSearchDesc(params)
	local found = ctx.catalog:findPhotos({ searchDesc = desc }) or {}
	local out = {}
	for index, photo in ipairs(found) do
		if index > MAX_FOUND then
			break
		end
		out[#out + 1] = {
			id = photo:getRawMetadata("uuid"),
			file_name = photo:getFormattedMetadata("fileName"),
			is_virtual_copy = photo:getRawMetadata("isVirtualCopy") and true or false,
		}
	end
	return { photos = list(out), count = #found, truncated = #found > MAX_FOUND }
end

function Commands.write.create_smart_collection(params, ctx)
	local desc = buildSearchDesc(params)
	local parts = splitPath(params.path, "path")
	local name = table.remove(parts)
	local parent, ok = findSet(ctx, parts)
	if not ok then
		fail("COLLECTION_NOT_FOUND", "The parent set does not exist: " .. table.concat(parts, "/") .. " (create it first)")
	end
	local existing = byName(childCollections(ctx, parent), name)
	if not existing then
		withWrite(ctx, "darkroom_ai: create smart collection", function()
			ctx.catalog:createSmartCollection(name, desc, parent, true)
		end)
	end
	local now = byName(childCollections(ctx, parent), name)
	if not now then
		fail("CREATE_FAILED", "Lightroom did not create the smart collection " .. name)
	end
	return { path = pathOf(now), created = existing == nil, photo_count = #(now:getPhotos() or {}) }
end

function Commands.write.build_smart_previews(params, ctx)
	local ids = idList(params, "photo_ids")
	local photos = {}
	for _, id in ipairs(ids) do
		photos[#photos + 1] = findWritablePhoto(ctx, id, false)
	end
	local ok, err = LrTasks.pcall(function()
		ctx.catalog:buildSmartPreviews(photos)
	end)
	if not ok then
		fail("SMART_PREVIEW_FAILED", "Lightroom could not build the smart previews: " .. tostring(err))
	end
	return { requested = #photos }
end

local function namedPresets(source)
	local names = {}
	for name in pairs(source or {}) do
		names[#names + 1] = name
	end
	table.sort(names)
	return list(names)
end

function Commands.read.list_metadata_presets(_, _)
	return { presets = namedPresets(LrApplication.metadataPresets()) }
end

function Commands.read.list_filename_presets(_, _)
	return { presets = namedPresets(LrApplication.filenamePresets()) }
end

-- Applies a metadata preset (for example a copyright template) to photos. It overwrites the fields
-- the preset fills in; the person must have agreed.
function Commands.write.apply_metadata_preset(params, ctx)
	local ids = idList(params, "photo_ids")
	local presets = LrApplication.metadataPresets() or {}
	local presetId = presets[params.preset]
	if not presetId then
		fail("PRESET_NOT_FOUND", "No metadata preset named " .. tostring(params.preset))
	end
	local photos = {}
	for _, id in ipairs(ids) do
		photos[#photos + 1] = findWritablePhoto(ctx, id, false)
	end
	withWrite(ctx, "darkroom_ai: metadata preset", function()
		for _, photo in ipairs(photos) do
			photo:applyMetadataPreset(presetId)
		end
	end)
	return { preset = params.preset, applied = #photos }
end


-- Lightroom's AI Enhance (Denoise, Raw Details, Super Resolution) and Reflection Removal, run on a
-- virtual copy through the Develop controller. They can take minutes and use the GPU. A snapshot is
-- taken first. Panel states before and after are returned. UNVERIFIED on this Lightroom version.
local ENHANCE_FEATURES = { denoise = true, rawDetails = true, superRes = true }

local function panelState(getter)
	local ok, value = LrTasks.pcall(getter)
	return ok and plainCopy(value, 3) or nil
end

local function waitFor(ctx, isDone, limitSeconds)
	local waited = 0
	while waited < limitSeconds do
		ctx.checkpoint()
		LrTasks.sleep(1)
		waited = waited + 1
		if isDone() then
			return true, waited
		end
	end
	return false, waited
end

function Commands.write.enhance(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	local feature = params.feature
	if not ENHANCE_FEATURES[feature] then
		fail("INVALID_PARAMS", "feature must be denoise, rawDetails or superRes")
	end
	local amount = params.amount
	if amount ~= nil and (type(amount) ~= "number" or amount < 1 or amount > 100) then
		fail("INVALID_PARAMS", "amount must be a number from 1 to 100")
	end
	local snapshot = savePointBefore(ctx, photo, "enhance " .. feature)
	showInDevelop(ctx, photo)
	local function state()
		return panelState(function()
			return LrDevelopController.getEnhancePanelState()
		end)
	end
	local before = state()
	local finished = false
	local ok, err = LrTasks.pcall(function()
		LrDevelopController.toggleEnhance(feature, amount, function()
			finished = true
		end, {})
	end)
	if not ok then
		fail("ENHANCE_FAILED", "Lightroom refused Enhance: " .. tostring(err))
	end
	local _, waited = waitFor(ctx, function()
		local now = state()
		return finished and not (now and now.enhanceIsRunning)
	end, tonumber(params.max_wait_s) or 300)
	return {
		photo_id = photo:getRawMetadata("uuid"),
		snapshot = snapshot,
		callback_called = finished,
		before = before,
		after = state(),
		waited_s = waited,
	}
end

local REFLECTION_QUALITY = { preview = true, standard = true, best = true }

function Commands.write.reflection_removal(params, ctx)
	local photo = findWritablePhoto(ctx, params.photo_id, true)
	local amount, quality = params.amount, params.quality or "preview"
	if amount ~= nil and (type(amount) ~= "number" or amount < -100 or amount > 100) then
		fail("INVALID_PARAMS", "amount must be a number from -100 to 100")
	end
	if not REFLECTION_QUALITY[quality] then
		fail("INVALID_PARAMS", "quality must be preview, standard or best")
	end
	local snapshot = savePointBefore(ctx, photo, "reflection removal")
	showInDevelop(ctx, photo)
	local function state()
		return panelState(function()
			return LrDevelopController.getReflectionRemovalPanelState()
		end)
	end
	local before = state()
	if before and before.isSupported == false then
		fail("ADOBE_FEATURE_UNAVAILABLE", "Lightroom says this photo does not support Reflection Removal")
	end
	local finished = false
	local ok, err = LrTasks.pcall(function()
		LrDevelopController.toggleReflectionRemoval(amount, quality, function()
			finished = true
		end, {})
	end)
	if not ok then
		fail("REFLECTION_FAILED", "Lightroom refused Reflection Removal: " .. tostring(err))
	end
	local _, waited = waitFor(ctx, function()
		return finished
	end, tonumber(params.max_wait_s) or 180)
	return {
		photo_id = photo:getRawMetadata("uuid"),
		snapshot = snapshot,
		callback_called = finished,
		before = before,
		after = state(),
		waited_s = waited,
	}
end

return Commands
