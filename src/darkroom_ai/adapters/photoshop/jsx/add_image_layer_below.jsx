// Adds a picture as a new bottom layer, scaled to cover the whole document (centre-cropped).
// Arguments: [0] absolute path of the background image (a working copy), [1] layer name.
(function (imagePath, layerName) {
    var doc = app.activeDocument;
    var width = doc.width.as("px");
    var height = doc.height.as("px");

    var source = app.open(new File(imagePath));
    if (source.bitsPerChannel != doc.bitsPerChannel) {
        source.bitsPerChannel = doc.bitsPerChannel;
    }
    if (source.mode != doc.mode) {
        source.changeMode(ChangeMode.RGB);
    }
    source.flatten();
    // Scale so the picture covers the document, then crop the overflow around the centre.
    var scale = Math.max(width / source.width.as("px"), height / source.height.as("px"));
    source.resizeImage(
        UnitValue(Math.ceil(source.width.as("px") * scale), "px"),
        UnitValue(Math.ceil(source.height.as("px") * scale), "px"),
        null,
        ResampleMethod.BICUBIC
    );
    source.resizeCanvas(UnitValue(width, "px"), UnitValue(height, "px"), AnchorPosition.MIDDLECENTER);
    var copied = source.activeLayer.duplicate(doc, ElementPlacement.PLACEATEND);
    source.close(SaveOptions.DONOTSAVECHANGES);

    app.activeDocument = doc;
    copied.name = layerName;
    return "ok";
})(arguments[0], arguments[1]);
