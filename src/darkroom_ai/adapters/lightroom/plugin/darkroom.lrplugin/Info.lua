--[[
darkroom_ai Bridge - Lightroom Classic plug-in.

Exposes a small set of JSON commands on two localhost sockets (LrSocket) so the local
darkroom_ai Python process can read and edit photos. No network calls, no process calls.
Structure follows Adobe's "remote_control_socket" SDK sample.
]]

return {
	LrSdkVersion = 6.0,
	LrSdkMinimumVersion = 6.0,

	LrToolkitIdentifier = "ai.darkroom.bridge",
	LrPluginName = "darkroom_ai Bridge",

	LrInitPlugin = "Init.lua",
	LrForceInitPlugin = true,
	LrDisablePlugin = "Stop.lua",
	LrShutdownPlugin = "Shutdown.lua",
	LrShutdownApp = "Shutdown.lua",

	VERSION = { major = 0, minor = 1, revision = 0, build = 1 },
}
