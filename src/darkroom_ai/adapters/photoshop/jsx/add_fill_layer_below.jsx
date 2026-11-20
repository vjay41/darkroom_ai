// Adds a new layer at the bottom of the stack and fills it with one colour.
// Arguments: [0] layer name, [1] red, [2] green, [3] blue (0-255).
(function (layerName, red, green, blue) {
    var doc = app.activeDocument;
    var bottom = doc.layers[doc.layers.length - 1];
    var fill = doc.artLayers.add();
    fill.name = layerName;
    fill.move(bottom, ElementPlacement.PLACEAFTER);
    doc.activeLayer = fill;
    var colour = new SolidColor();
    colour.rgb.red = Number(red);
    colour.rgb.green = Number(green);
    colour.rgb.blue = Number(blue);
    doc.selection.selectAll();
    doc.selection.fill(colour);
    doc.selection.deselect();
    return "ok";
})(arguments[0], arguments[1], arguments[2], arguments[3]);
