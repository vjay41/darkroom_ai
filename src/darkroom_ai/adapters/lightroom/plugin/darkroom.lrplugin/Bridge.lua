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
local LrDate = import "LrDate"
local LrFileUtils = import "LrFileUtils"
local LrPrefs = import "LrPrefs"
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
local MAX_WAITING = 2 -- requests allowed to wait behind the one being processed
local MAX_RESPONSE = 8 * 1024 * 1024 -- a larger answer is replaced by an error, never sent
-- Time one command may run before it is cut off (the client's own deadline can make it shorter).
-- A command checks ctx.checkpoint() in its loops and stops itself; a watchdog answers the client
-- if a command does not return at all, and then refuses new work instead of queueing it.
local DEFAULT_BUDGET_S = 60
local BUDGETS = {
	export_tiff = 300,
	import_folder = 300,
	export_preview = 120,
	create_mask = 90,
	set_mask_values = 90,
}
-- LrDate.currentTime() counts seconds from 2001-01-01; client deadlines are Unix seconds.
local UNIX_OFFSET = 978307200
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

local function errorResponse(id, code, message, retryAfter)
	return { id = id, ok = false, error = { code = code, message = message, retry_after_s = retryAfter } }
end

-- Runs inside an LrTasks task. Returns the response table for one decoded request.
local function handleRequest(request, config, instance, job)
	local id = type(request.id) == "string" and request.id or nil
	if not id or type(request.command) ~= "string" then
		return errorResponse(id, "BAD_REQUEST", "Request needs string fields 'id' and 'command'")
	end
	if request.token ~= config.token then
		logger:warn("rejected request with a wrong or missing token")
		return errorResponse(id, "UNAUTHORIZED", "Missing or wrong token")
	end

	-- A request carries the time its client stops waiting. After that nobody can use the answer
	-- (and the client may already have asked again), so it is not executed.
	if type(request.deadline) == "number" and LrDate.currentTime() + UNIX_OFFSET > request.deadline then
		logger:warn("[" .. instance .. "] skipped expired request for '" .. request.command .. "'")
		return errorResponse(
			id,
			"REQUEST_EXPIRED",
			"The client stopped waiting for this request before Lightroom reached it; nothing was executed"
		)
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
		instance = instance,
		-- Called inside long loops: stops the command once it has used up its time.
		checkpoint = function()
			if job.timedOut or LrDate.currentTime() - job.since > job.budget then
				job.timedOut = true
				error({
					code = "COMMAND_TIMEOUT",
					message = "'" .. command .. "' used its " .. job.budget .. " s time limit and was stopped",
				}, 0)
			end
		end,
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
	local startedAt = LrDate.currentTime()
	local ok, result = LrTasks.pcall(handler, params, ctx)
	local took = string.format("%.1f s", LrDate.currentTime() - startedAt)
	if ok then
		logger:info("[" .. instance .. "] command '" .. command .. "' ok (" .. took .. ")")
		return { id = id, ok = true, result = result or {} }
	end
	if type(result) == "table" and result.code then
		logger:warn("[" .. instance .. "] command '" .. command .. "' failed: " .. tostring(result.code) .. " (" .. took .. ")")
		return errorResponse(id, result.code, tostring(result.message))
	end
	logger:error("[" .. instance .. "] command '" .. command .. "' raised: " .. tostring(result) .. " (" .. took .. ")")
	return errorResponse(id, "INTERNAL", tostring(result))
end

-- One listening session: bind both sockets, serve until a socket closes or the plug-in stops.
local function runSession(config, active, instance)
	LrFunctionContext.callWithContext("darkroom_ai_bridge_session", function(context)
		local session = {
			closed = false,
			buffer = "",
			queue = {},
			working = false,
			current = nil,
			handled = 0,
			failed = 0,
			lastError = nil,
			startedAt = LrDate.currentTime(),
		}
		local sender

		local function send(response)
			local ok, text = pcall(Json.encode, response)
			if not ok then
				text = Json.encode(errorResponse(response.id, "INTERNAL", "Response could not be encoded: " .. tostring(text)))
			end
			if #text > MAX_RESPONSE then
				text = Json.encode(
					errorResponse(response.id, "RESPONSE_TOO_LARGE", "The answer is larger than 8 MB; ask for less at a time")
				)
			end
			sender:send(text .. "\n")
		end

		local function snapshot()
			local job = session.current
			local busy = nil
			if job then
				local running = LrDate.currentTime() - job.since
				busy = {
					command = job.command,
					running_s = running,
					budget_s = job.budget,
					over_budget = job.answered == true,
				}
			end
			return {
				instance = instance,
				plugin_version = Commands.read.list_commands().plugin_version,
				uptime_s = LrDate.currentTime() - session.startedAt,
				waiting = #session.queue,
				busy = busy,
				handled = session.handled,
				failed = session.failed,
				last_error = session.lastError,
			}
		end

		local function pump()
			if session.working then
				return
			end
			session.working = true
			LrTasks.startAsyncTask(function()
				while #session.queue > 0 and not session.closed do
					local request = table.remove(session.queue, 1)
					local response
					if not active() then
						-- A newer instance (plug-in reload) or a stop took over: do not run work
						-- queued here with this instance's (possibly old) code.
						response = errorResponse(
							request.id,
							"BRIDGE_RESTARTING",
							"The Lightroom plug-in was restarting; nothing was executed, retry in a few seconds"
						)
					else
						local budget = BUDGETS[request.command] or DEFAULT_BUDGET_S
						if type(request.deadline) == "number" then
							local left = request.deadline - (LrDate.currentTime() + UNIX_OFFSET)
							budget = math.min(budget, math.max(left, 1))
						end
						local job = {
							id = request.id,
							command = tostring(request.command),
							since = LrDate.currentTime(),
							budget = budget,
							timedOut = false,
							answered = false,
						}
						session.current = job
						local ok, handled = LrTasks.pcall(handleRequest, request, config, instance, job)
						session.current = nil
						if job.answered then
							-- The watchdog already told the client this command timed out.
							logger:warn("[" .. instance .. "] '" .. job.command .. "' finished late; its answer is discarded")
							response = nil
						elseif ok then
							response = handled
						else
							response = errorResponse(request.id, "INTERNAL", tostring(handled))
						end
					end
					if response then
						session.handled = session.handled + 1
						if response.ok ~= true then
							session.failed = session.failed + 1
							session.lastError = response.error and response.error.code or nil
						end
						local sent, sendError = pcall(send, response)
						if not sent then
							logger:error("could not send response: " .. tostring(sendError))
						end
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
						local requestId = type(request.id) == "string" and request.id or nil
						local busy = session.current
						if request.command == "bridge_status" then
							-- Answered at once, never queued, so it works even while a command is stuck.
							local reply
							if request.token ~= config.token then
								reply = errorResponse(requestId, "UNAUTHORIZED", "Missing or wrong token")
							else
								reply = { id = requestId, ok = true, result = snapshot() }
							end
							pcall(send, reply)
						elseif #session.queue >= MAX_WAITING or (busy and busy.answered) then
							local what = busy and (" while running '" .. tostring(busy.command) .. "'") or ""
							local wait = 5
							if busy then
								wait = math.max(2, math.ceil(busy.budget - (LrDate.currentTime() - busy.since)))
							end
							pcall(send, errorResponse(
								requestId,
								"BRIDGE_BUSY",
								"The Lightroom plug-in is still busy" .. what .. "; nothing was executed",
								wait
							))
						else
							session.queue[#session.queue + 1] = request
						end
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

		logger:info("[" .. instance .. "] listening on localhost ports " .. config.request_port .. " (requests) and " .. config.response_port .. " (responses)")

		-- Watchdog: if a command does not come back in its time, answer the client now and mark the
		-- session as wedged, so new requests are refused with a clear message instead of queueing.
		LrTasks.startAsyncTask(function()
			while not session.closed do
				LrTasks.sleep(0.5)
				local job = session.current
				if job and not job.answered and LrDate.currentTime() - job.since > job.budget + 2 then
					job.answered = true
					job.timedOut = true
					logger:warn("[" .. instance .. "] command '" .. job.command .. "' exceeded " .. job.budget .. " s")
					pcall(send, errorResponse(
						job.id,
						"COMMAND_TIMEOUT",
						"'" .. job.command .. "' did not finish within " .. job.budget
							.. " s and may still complete inside Lightroom; check the photo's state before repeating it"
					))
				end
			end
		end, "darkroom_ai watchdog")

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

-- Each start claims a new instance id in the plug-in preferences. Observed on Lightroom 15.2.1:
-- "Reload Plug-in" gives the new code a fresh global table while the old run loop keeps running,
-- so globals cannot be used to stop it. The preferences are shared, so every run loop ends as
-- soon as a newer instance id appears (or when this instance is told to stop).
function Bridge.start()
	local bridge = state()
	local prefs = LrPrefs.prefsForPlugin()
	local instance = string.format("%.3f-%d", LrDate.currentTime(), math.random(1, 1000000))
	prefs.activeInstance = instance

	LrTasks.startAsyncTask(function()
		-- Give a previous instance time to notice the new id and close its sockets.
		LrTasks.sleep(1.5)
		bridge.stop = false
		bridge.running = true
		local generation = instance
		logger:info("[" .. instance .. "] bridge starting")

		local function active()
			return not bridge.stop and prefs.activeInstance == instance
		end

		while active() do
			-- bridge.json is re-read for every session so a new token or catalog takes effect
			-- as soon as the client reconnects.
			local config, reason = loadConfig()
			if config then
				local ok, err = LrTasks.pcall(runSession, config, active, instance)
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
		bridge.running = false
		logger:info("[" .. generation .. "] bridge stopped")
	end, "darkroom_ai bridge")
end

function Bridge.stop()
	state().stop = true
end

function Bridge.isRunning()
	return state().running
end

return Bridge
