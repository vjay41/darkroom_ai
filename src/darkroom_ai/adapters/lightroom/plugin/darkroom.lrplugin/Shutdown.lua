-- Shutdown.lua - runs when the plug-in is removed/reloaded or Lightroom quits.
-- Follows Adobe's "remote_control_socket" sample: stop the run loop, wait briefly, report done.

local LrDate = import "LrDate"
local LrTasks = import "LrTasks"

return {
	LrShutdownFunction = function(doneFunction, progressFunction)
		-- Lightroom raises an error when an undefined global is read, so read it under pcall.
		local ok, bridge = pcall(function()
			return _G.DarkroomBridge
		end)
		local wasRunning = ok and type(bridge) == "table" and bridge.running
		if wasRunning then
			-- Set the flag right away (not inside the task below) so that a following
			-- plug-in init, which waits before it starts, is not stopped by mistake.
			bridge.stop = true
		end

		LrTasks.startAsyncTask(function()
			if wasRunning then
				local start = LrDate.currentTime()
				-- Wait up to 1 second for the run loop to end.
				while bridge.running and bridge.stop and (LrDate.currentTime() - start) < 1 do
					progressFunction(math.min(1, LrDate.currentTime() - start))
					LrTasks.sleep(0.1)
				end
			end
			progressFunction(1)
			doneFunction()
		end)
	end,
}
