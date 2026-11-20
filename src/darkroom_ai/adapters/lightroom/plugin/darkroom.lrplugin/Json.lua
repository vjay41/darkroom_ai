--[[
Json.lua - small JSON encoder/decoder for Lua 5.1 (the Lightroom SDK has none).

encode(value)  -> string            (errors on cycles deeper than MAX_DEPTH)
decode(string) -> value | nil, err

Arrays: a table with t[1] ~= nil is an array. An empty table is encoded as {} unless it is
Json.EMPTY_ARRAY. JSON null decodes to nil. Functions/userdata are skipped when encoding.
]]

local Json = {}

Json.EMPTY_ARRAY = {}

local MAX_DEPTH = 32

local ESCAPES = {
	['"'] = '\\"',
	["\\"] = "\\\\",
	["\b"] = "\\b",
	["\f"] = "\\f",
	["\n"] = "\\n",
	["\r"] = "\\r",
	["\t"] = "\\t",
}

local function escapeChar(c)
	return ESCAPES[c] or string.format("\\u%04x", string.byte(c))
end

local function encodeString(s)
	return '"' .. string.gsub(s, '[%c"\\]', escapeChar) .. '"'
end

local function encodeNumber(n)
	if n ~= n or n == math.huge or n == -math.huge then
		return "null"
	end
	local text
	if n == math.floor(n) and math.abs(n) < 1e15 then
		text = string.format("%d", n)
	else
		text = string.format("%.14g", n)
	end
	-- Guard against a host locale that prints a decimal comma.
	return (string.gsub(text, ",", "."))
end

local encodeValue

local function isEncodable(v)
	local t = type(v)
	return t == "string" or t == "number" or t == "boolean" or t == "table"
end

local function encodeTable(t, depth)
	if depth > MAX_DEPTH then
		error("Json.encode: table nested too deeply (cycle?)", 0)
	end
	if t == Json.EMPTY_ARRAY then
		return "[]"
	end
	local parts = {}
	if t[1] ~= nil then
		for i = 1, #t do
			local v = t[i]
			parts[#parts + 1] = isEncodable(v) and encodeValue(v, depth + 1) or "null"
		end
		return "[" .. table.concat(parts, ",") .. "]"
	end
	local keys = {}
	for k, v in pairs(t) do
		if (type(k) == "string" or type(k) == "number") and isEncodable(v) then
			keys[#keys + 1] = k
		end
	end
	table.sort(keys, function(a, b)
		return tostring(a) < tostring(b)
	end)
	for _, k in ipairs(keys) do
		parts[#parts + 1] = encodeString(tostring(k)) .. ":" .. encodeValue(t[k], depth + 1)
	end
	return "{" .. table.concat(parts, ",") .. "}"
end

encodeValue = function(v, depth)
	local t = type(v)
	if t == "string" then
		return encodeString(v)
	elseif t == "number" then
		return encodeNumber(v)
	elseif t == "boolean" then
		return v and "true" or "false"
	elseif t == "table" then
		return encodeTable(v, depth)
	end
	return "null"
end

function Json.encode(value)
	return encodeValue(value, 1)
end

--------------------------------------------------------------------------------
-- Decoder (recursive descent). `pos` is the index of the next unread character.

local function decodeError(message, pos)
	error(string.format("Json.decode: %s at character %d", message, pos), 0)
end

local function skipWhitespace(s, pos)
	local _, last = string.find(s, "^[ \t\r\n]*", pos)
	return last + 1
end

local function utf8Char(code)
	if code < 0x80 then
		return string.char(code)
	elseif code < 0x800 then
		return string.char(0xC0 + math.floor(code / 0x40), 0x80 + code % 0x40)
	elseif code < 0x10000 then
		return string.char(
			0xE0 + math.floor(code / 0x1000),
			0x80 + math.floor(code / 0x40) % 0x40,
			0x80 + code % 0x40
		)
	end
	return string.char(
		0xF0 + math.floor(code / 0x40000),
		0x80 + math.floor(code / 0x1000) % 0x40,
		0x80 + math.floor(code / 0x40) % 0x40,
		0x80 + code % 0x40
	)
end

local UNESCAPES = {
	['"'] = '"',
	["\\"] = "\\",
	["/"] = "/",
	b = "\b",
	f = "\f",
	n = "\n",
	r = "\r",
	t = "\t",
}

local function decodeString(s, pos)
	-- s:sub(pos, pos) is the opening quote
	local parts = {}
	local i = pos + 1
	while true do
		local stop = string.find(s, '["\\]', i)
		if not stop then
			decodeError("unterminated string", pos)
		end
		parts[#parts + 1] = string.sub(s, i, stop - 1)
		local c = string.sub(s, stop, stop)
		if c == '"' then
			return table.concat(parts), stop + 1
		end
		local e = string.sub(s, stop + 1, stop + 1)
		if e == "u" then
			local hex = string.match(s, "^%x%x%x%x", stop + 2)
			if not hex then
				decodeError("bad \\u escape", stop)
			end
			local code = tonumber(hex, 16)
			i = stop + 6
			if code >= 0xD800 and code <= 0xDBFF then
				local low = string.match(s, "^\\u(%x%x%x%x)", i)
				local lowCode = low and tonumber(low, 16)
				if lowCode and lowCode >= 0xDC00 and lowCode <= 0xDFFF then
					code = 0x10000 + (code - 0xD800) * 0x400 + (lowCode - 0xDC00)
					i = i + 6
				end
			end
			parts[#parts + 1] = utf8Char(code)
		elseif UNESCAPES[e] then
			parts[#parts + 1] = UNESCAPES[e]
			i = stop + 2
		else
			decodeError("bad escape", stop)
		end
	end
end

local function decodeNumber(s, pos)
	local text = string.match(s, "^-?%d+%.?%d*[eE]?[+-]?%d*", pos)
	if not text then
		decodeError("bad number", pos)
	end
	local n = tonumber(text)
	if not n then
		-- A host locale with a decimal comma makes tonumber("1.5") fail.
		n = tonumber((string.gsub(text, "%.", ",")))
	end
	if not n then
		decodeError("bad number", pos)
	end
	return n, pos + #text
end

local decodeValue

local function decodeArray(s, pos, depth)
	local result = {}
	local i = skipWhitespace(s, pos + 1)
	if string.sub(s, i, i) == "]" then
		return result, i + 1
	end
	while true do
		local value
		value, i = decodeValue(s, i, depth + 1)
		if value ~= nil then
			result[#result + 1] = value
		end
		i = skipWhitespace(s, i)
		local c = string.sub(s, i, i)
		if c == "]" then
			return result, i + 1
		elseif c ~= "," then
			decodeError("expected ',' or ']'", i)
		end
		i = skipWhitespace(s, i + 1)
	end
end

local function decodeObject(s, pos, depth)
	local result = {}
	local i = skipWhitespace(s, pos + 1)
	if string.sub(s, i, i) == "}" then
		return result, i + 1
	end
	while true do
		if string.sub(s, i, i) ~= '"' then
			decodeError("expected a string key", i)
		end
		local key
		key, i = decodeString(s, i)
		i = skipWhitespace(s, i)
		if string.sub(s, i, i) ~= ":" then
			decodeError("expected ':'", i)
		end
		i = skipWhitespace(s, i + 1)
		local value
		value, i = decodeValue(s, i, depth + 1)
		result[key] = value
		i = skipWhitespace(s, i)
		local c = string.sub(s, i, i)
		if c == "}" then
			return result, i + 1
		elseif c ~= "," then
			decodeError("expected ',' or '}'", i)
		end
		i = skipWhitespace(s, i + 1)
	end
end

decodeValue = function(s, pos, depth)
	if depth > MAX_DEPTH then
		decodeError("nested too deeply", pos)
	end
	local c = string.sub(s, pos, pos)
	if c == "{" then
		return decodeObject(s, pos, depth)
	elseif c == "[" then
		return decodeArray(s, pos, depth)
	elseif c == '"' then
		return decodeString(s, pos)
	elseif string.sub(s, pos, pos + 3) == "true" then
		return true, pos + 4
	elseif string.sub(s, pos, pos + 4) == "false" then
		return false, pos + 5
	elseif string.sub(s, pos, pos + 3) == "null" then
		return nil, pos + 4
	elseif c == "-" or string.find(c, "^%d$") then
		return decodeNumber(s, pos)
	end
	decodeError("unexpected character", pos)
end

-- Returns value, nil on success; nil, message on failure.
function Json.decode(s)
	if type(s) ~= "string" then
		return nil, "Json.decode: input is not a string"
	end
	local ok, value, nextPos = pcall(function()
		local start = skipWhitespace(s, 1)
		local v, p = decodeValue(s, start, 1)
		return v, p
	end)
	if not ok then
		return nil, tostring(value)
	end
	local rest = skipWhitespace(s, nextPos)
	if rest <= #s then
		return nil, string.format("Json.decode: unexpected text at character %d", rest)
	end
	return value, nil
end

return Json
