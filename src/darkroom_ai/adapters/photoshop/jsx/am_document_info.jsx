// Action Manager: reads properties of the active document with executeActionGet.
(function () {
    function quote(text) {
        return '"' + String(text).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
    }
    var ref = new ActionReference();
    ref.putEnumerated(charIDToTypeID("Dcmn"), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
    var desc = executeActionGet(ref);
    var parts = [
        '"title":' + quote(desc.getString(stringIDToTypeID("title"))),
        '"document_id":' + desc.getInteger(stringIDToTypeID("documentID")),
        '"number_of_layers":' + desc.getInteger(stringIDToTypeID("numberOfLayers")),
        '"has_background_layer":' + (desc.getBoolean(stringIDToTypeID("hasBackgroundLayer")) ? "true" : "false")
    ];
    return "{" + parts.join(",") + "}";
})();
