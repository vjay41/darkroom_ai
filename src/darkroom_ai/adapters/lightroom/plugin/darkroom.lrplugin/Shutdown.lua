-- Shutdown.lua - runs when the plug-in is removed/reloaded or Lightroom quits.
-- Follows Adobe's "remote_control_socket" sample: stop the run loop, wait briefly, report done.

local LrDate = import "LrDate"
local LrTasks = import "LrTasks"

return {
	LrShutdownFunction = function(doneFunction, progressFunction)
		LrTasks.startAsyncTask(function()
			local bridge = _G.DarkroomBridge
			if bridge and bridge.running then
				bridge.stop = true
				local start = LrDate.currentTime()
				-- Wait up to 2 seconds for the run loop to end.
				while bridge.running and (LrDate.currentTime() - start) < 2 do
					progressFunction(math.min(1, (LrDate.currentTime() - start) / 2))
					LrTasks.sleep(0.1)
				end
			end
			progressFunction(1)
			doneFunction()
		end)
	end,
}
