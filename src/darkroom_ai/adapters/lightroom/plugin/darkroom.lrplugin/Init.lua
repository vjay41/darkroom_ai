-- Init.lua - runs when Lightroom loads the plug-in; starts the localhost bridge.
-- Any start-up failure is written to the plug-in log (darkroom_ai.log) instead of being lost.

local LrLogger = import "LrLogger"

local logger = LrLogger("darkroom_ai")
logger:enable("logfile")
logger:info("plug-in init")

local ok, err = pcall(function()
	local Bridge = require "Bridge"
	Bridge.start()
end)
if not ok then
	logger:error("plug-in init failed: " .. tostring(err))
end
