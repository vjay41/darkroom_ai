-- Stop.lua - runs when the plug-in is disabled in the Plug-in Manager; stops the bridge.

if _G.DarkroomBridge then
	_G.DarkroomBridge.stop = true
end
