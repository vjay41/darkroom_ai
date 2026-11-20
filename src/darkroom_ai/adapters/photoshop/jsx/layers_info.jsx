// Returns the layers of the active document, top to bottom, as JSON:
// [{"name": "...", "has_mask": true, "is_background": false}, ...]
// has_mask is read with Action Manager (hasUserMask).
(function () {
    function quote(text) {
        return '"' + String(text).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
    }
    function hasUserMask(layer) {
        var ref = new ActionReference();
        ref.putIdentifier(charIDToTypeID("Lyr "), layer.id);
        return executeActionGet(ref).getBoolean(stringIDToTypeID("hasUserMask"));
    }
    var doc = app.activeDocument;
    var items = [];
    for (var i = 0; i < doc.layers.length; i++) {
        var layer = doc.layers[i];
        items.push("{" + [
            '"name":' + quote(layer.name),
            '"has_mask":' + (hasUserMask(layer) ? "true" : "false"),
            '"is_background":' + (layer.isBackgroundLayer ? "true" : "false")
        ].join(",") + "}");
    }
    return "[" + items.join(",") + "]";
})();
