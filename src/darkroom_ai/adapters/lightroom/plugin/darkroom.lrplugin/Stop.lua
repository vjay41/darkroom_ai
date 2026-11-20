-- Stop.lua - runs when the plug-in is disabled in the Plug-in Manager; stops the bridge.

-- Lightroom raises an error when an undefined global is read, so read it under pcall.
local ok, bridge = pcall(function()
	return _G.DarkroomBridge
end)
if ok and type(bridge) == "table" then
	bridge.stop = true
end
