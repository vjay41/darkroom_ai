-- Stop.lua - runs when the plug-in is disabled in the Plug-in Manager, and from the menu item
-- "Stop darkroom_ai Bridge". Stops every running bridge instance.

local LrDialogs = import "LrDialogs"
local LrPrefs = import "LrPrefs"

-- Run loops compare this value with their own instance id (see Bridge.lua).
LrPrefs.prefsForPlugin().activeInstance = "stopped"

-- Lightroom raises an error when an undefined global is read, so read it under pcall.
local ok, bridge = pcall(function()
	return _G.DarkroomBridge
end)
if ok and type(bridge) == "table" then
	bridge.stop = true
end

LrDialogs.showBezel("darkroom_ai Bridge stopped", 2)
