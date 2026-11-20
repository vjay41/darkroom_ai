--[[
Bridge.lua - localhost socket server for the darkroom_ai bridge.

Two LrSocket connections (LrSocket only listens on localhost):
  request port  (mode "receive"): the client sends one JSON request per line
  response port (mode "send"):    the plug-in sends one JSON response per line

Request : {"id": "...", "token": "...", "command": "...", "params": {...}}
Response: {"id": "...", "ok": true,  "result": {...}}
          {"id": "...", "ok": false, "error": {"code": "...", "message": "..."}}

Safety:
- Every request must carry the token from bridge.json (in this plug-in folder).
- Write commands are refused unless the open catalog's path equals catalog_path in bridge.json.
- Requests are processed one at a time, in order.
- Parameters and the token are never written to the log.
]]

local LrApplication = import "LrApplication"
local LrFileUtils = import "LrFileUtils"
local LrFunctionContext = import "LrFunctionContext"
local LrLogger = import "LrLogger"
local LrPathUtils = import "LrPathUtils"
local LrSocket = import "LrSocket"
local LrTasks = import "LrTasks"

local Commands = require "Commands"
local Json = require "Json"

local logger = LrLogger("darkroom_ai")
logger:enable("logfile")

local MAX_BUFFER = 4 * 1024 * 1024
local MIN_TOKEN_LENGTH = 32

local Bridge = {}

local function state()
	-- Lightroom raises an error when an undefined global is read, so read it under pcall.
	local ok, existing = pcall(function()
		return _G.DarkroomBridge
	end)
	if ok and type(existing) == "table" then
		return existing
	end
	local fresh = { running = false, stop = false }
	_G.DarkroomBridge = fresh
	return fresh
end

local function normalizePath(path)
	if type(path) ~= "string" then
		return nil
	end
	local p = string.lower(string.gsub(path, "/", "\\"))
	return (string.gsub(p, "\\+$", ""))
end

local function isPort(value)
	return type(value) == "number" and value >= 1024 and value <= 65535 and value == math.floor(value)
end

-- Reads bridge.json from the plug-in folder. Returns config, nil or nil, reason.
local function loadConfig()
	local path = LrPathUtils.child(_PLUGIN.path, "bridge.json")
	if LrFileUtils.exists(path) ~= "file" then
		return nil, "bridge.json not found in the plug-in folder (run `darkroom lightroom setup`)"
	end
	local ok, text = pcall(LrFileUtils.readFile, path)
	if not ok or type(text) ~= "string" then
		return nil, "bridge.json could not be read"
	end
	local config, err = Json.decode(text)
	if type(config) ~= "table" then
		return nil, "bridge.json is not valid JSON: " .. tostring(err)
	end
	if type(config.token) ~= "string" or #config.token < MIN_TOKEN_LENGTH then
		return nil, "bridge.json: token is missing or too short"
	end
	if type(config.catalog_path) ~= "string" or config.catalog_path == "" then
		return nil, "bridge.json: catalog_path is missing"
	end
	if type(config.preview_dir) ~= "string" or config.preview_dir == "" then
		return nil, "bridge.json: preview_dir is missing"
	end
	if not isPort(config.request_port) or not isPort(config.response_port) then
		return nil, "bridge.json: request_port / response_port must be numbers between 1024 and 65535"
	end
	return config, nil
end

local function errorResponse(id, code, message)
	return { id = id, ok = false, error = { code = code, message = message } }
end

-- Runs inside an LrTasks task. Returns the response table for one decoded request.
local function handleRequest(request, config)
	local id = type(request.id) == "string" and request.id or nil
	if not id or type(request.command) ~= "string" then
		return errorResponse(id, "BAD_REQUEST", "Request needs string fields 'id' and 'command'")
	end
	if request.token ~= config.token then
		logger:warn("rejected request with a wrong or missing token")
		return errorResponse(id, "UNAUTHORIZED", "Missing or wrong token")
	end

	local command = request.command
	local isWrite = Commands.write[command] ~= nil
	local handler = Commands.write[command] or Commands.read[command]
	if not handler then
		return errorResponse(id, "UNKNOWN_COMMAND", "Unknown command: " .. command)
	end

	local catalog = LrApplication.activeCatalog()
	local catalogPath = catalog:getPath()
	local ctx = {
		config = config,
		catalog = catalog,
		catalog_path = catalogPath,
		writes_allowed = normalizePath(catalogPath) == normalizePath(config.catalog_path),
	}

	if isWrite and not ctx.writes_allowed then
		logger:warn("refused write command '" .. command .. "': open catalog is not the configured catalog")
		return errorResponse(
			id,
			"WRONG_CATALOG",
			"Refused: the open catalog (" .. tostring(catalogPath) .. ") is not the configured catalog ("
				.. config.catalog_path
				.. ")"
		)
	end

	local params = type(request.params) == "table" and request.params or {}
	local ok, result = LrTasks.pcall(handler, params, ctx)
	if ok then
		logger:info("command '" .. command .. "' ok")
		return { id = id, ok = true, result = result or {} }
	end
	if type(result) == "table" and result.code then
		logger:warn("command '" .. command .. "' failed: " .. tostring(result.code))
		return errorResponse(id, result.code, tostring(result.message))
	end
	logger:error("command '" .. command .. "' raised: " .. tostring(result))
	return errorResponse(id, "INTERNAL", tostring(result))
end

-- One listening session: bind both sockets, serve until a socket closes or the plug-in stops.
local function runSession(config, active)
	LrFunctionContext.callWithContext("darkroom_ai_bridge_session", function(context)
		local session = { closed = false, buffer = "", queue = {}, working = false }
		local sender

		local function send(response)
			local ok, text = pcall(Json.encode, response)
			if not ok then
				text = Json.encode(errorResponse(response.id, "INTERNAL", "Response could not be encoded: " .. tostring(text)))
			end
			sender:send(text .. "\n")
		end

		local function pump()
			if session.working then
				return
			end
			session.working = true
			LrTasks.startAsyncTask(function()
				while #session.queue > 0 and not session.closed do
					local request = table.remove(session.queue, 1)
					local ok, response = LrTasks.pcall(handleRequest, request, config)
					if not ok then
						response = errorResponse(request.id, "INTERNAL", tostring(response))
					end
					local sent, sendError = pcall(send, response)
					if not sent then
						logger:error("could not send response: " .. tostring(sendError))
					end
				end
				session.working = false
			end)
		end

		-- LrSocket may deliver one line per call or arbitrary fragments; handle both.
		local function onMessage(message)
			if type(message) ~= "string" then
				return
			end
			for piece in string.gmatch(message .. "\n", "([^\n]*)\n") do
				if piece ~= "" then
					local request = Json.decode(piece)
					if type(request) ~= "table" and session.buffer ~= "" then
						request = Json.decode(session.buffer .. piece)
					end
					if type(request) == "table" then
						session.buffer = ""
						session.queue[#session.queue + 1] = request
					elseif #session.buffer + #piece <= MAX_BUFFER then
						session.buffer = session.buffer .. piece
					else
						session.buffer = ""
						logger:warn("dropped an oversized or malformed request")
					end
				end
			end
			pump()
		end

		local function onError(socket, err)
			if err == "timeout" then
				socket:reconnect()
			else
				logger:warn("socket error: " .. tostring(err))
				session.closed = true
			end
		end

		sender = LrSocket.bind({
			functionContext = context,
			plugin = _PLUGIN,
			port = config.response_port,
			mode = "send",
			onClosed = function()
				session.closed = true
			end,
			onError = onError,
		})

		local receiver = LrSocket.bind({
			functionContext = context,
			plugin = _PLUGIN,
			port = config.request_port,
			mode = "receive",
			onMessage = function(_, message)
				onMessage(message)
			end,
			onClosed = function()
				session.closed = true
			end,
			onError = onError,
		})

		logger:info("listening on localhost ports " .. config.request_port .. " (requests) and " .. config.response_port .. " (responses)")

		while active() and not session.closed do
			LrTasks.sleep(0.2)
		end

		session.closed = true
		pcall(function()
			sender:close()
		end)
		pcall(function()
			receiver:close()
		end)
	end)
end

-- Each start claims a new generation. A run loop ends when it is told to stop or when a newer
-- generation exists, so "Reload Plug-in" (old shutdown + new init, in either order) leaves
-- exactly one loop running.
function Bridge.start()
	local bridge = state()

	LrTasks.startAsyncTask(function()
		-- On a reload, give the previous instance's shutdown time to finish first.
		LrTasks.sleep(1.5)
		bridge.generation = (bridge.generation or 0) + 1
		local generation = bridge.generation
		bridge.stop = false
		bridge.running = true
		logger:info("bridge starting (generation " .. generation .. ")")

		local function active()
			return not bridge.stop and bridge.generation == generation
		end

		while active() do
			-- bridge.json is re-read for every session so a new token or catalog takes effect
			-- as soon as the client reconnects.
			local config, reason = loadConfig()
			if config then
				local ok, err = LrTasks.pcall(runSession, config, active)
				if not ok then
					logger:error("session ended with an error: " .. tostring(err))
					LrTasks.sleep(2)
				end
			else
				logger:warn("not listening: " .. tostring(reason))
				LrTasks.sleep(3)
			end
			LrTasks.sleep(0.3)
		end
		if bridge.generation == generation then
			bridge.running = false
		end
		logger:info("bridge stopped (generation " .. generation .. ")")
	end, "darkroom_ai bridge")
end

function Bridge.stop()
	state().stop = true
end

function Bridge.isRunning()
	return state().running
end

return Bridge
