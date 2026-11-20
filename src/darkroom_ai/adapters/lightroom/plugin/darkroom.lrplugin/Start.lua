-- Start.lua - menu item "Start darkroom_ai Bridge" (File > Plug-in Extras).
-- Starts a fresh bridge instance; any older instance stops by itself.

local LrDialogs = import "LrDialogs"

local Bridge = require "Bridge"

Bridge.start()
LrDialogs.showBezel("darkroom_ai Bridge starting", 2)
