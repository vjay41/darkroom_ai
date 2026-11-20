// Returns facts about the active document as a JSON string (ExtendScript has no JSON object).
(function () {
    function quote(text) {
        return '"' + String(text).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
    }
    var doc = app.activeDocument;
    var bits = { "BitsPerChannelType.ONE": 1, "BitsPerChannelType.EIGHT": 8,
                 "BitsPerChannelType.SIXTEEN": 16, "BitsPerChannelType.THIRTYTWO": 32 };
    var parts = [
        '"name":' + quote(doc.name),
        '"width":' + Math.round(doc.width.as("px")),
        '"height":' + Math.round(doc.height.as("px")),
        '"bits":' + (bits[String(doc.bitsPerChannel)] || 0),
        '"mode":' + quote(String(doc.mode)),
        '"layers":' + doc.layers.length
    ];
    return "{" + parts.join(",") + "}";
})();
