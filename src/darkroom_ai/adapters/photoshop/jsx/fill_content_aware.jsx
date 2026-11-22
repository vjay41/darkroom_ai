// Loads a grayscale PNG (white = area to fill) as the selection, grows it by a few pixels, and
// fills it with Content-Aware Fill on a NEW layer above the picture, so the original pixels stay
// underneath. Does not use the clipboard.
// Arguments: [0] absolute path of the mask PNG, [1] pixels to grow the selection, [2] layer name.
(function (maskPath, growPx, layerName) {
    var doc = app.activeDocument;
    var base = doc.activeLayer;
    var maskDoc = app.open(new File(maskPath));
    var sameSize = Math.round(maskDoc.width.as("px")) === Math.round(doc.width.as("px")) &&
        Math.round(maskDoc.height.as("px")) === Math.round(doc.height.as("px"));
    if (!sameSize) {
        maskDoc.close(SaveOptions.DONOTSAVECHANGES);
        throw new Error("The mask is not the same pixel size as the document.");
    }
    // Copy the mask into the picture's document as an alpha channel and load it as the selection
    // (white = selected). No clipboard and no layer visibility changes.
    var maskChannel = maskDoc.channels[0].duplicate(doc);
    maskDoc.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = doc;
    doc.selection.load(maskChannel, SelectionType.REPLACE, false);
    maskChannel.remove();
    doc.activeLayer = base;

    if (Number(growPx) > 0) {
        doc.selection.expand(UnitValue(Number(growPx), "px"));
    }
    // Sample from the picture, fill on a new transparent layer.
    var filled = base.duplicate();
    filled.name = layerName;
    doc.activeLayer = filled;
    var fillDesc = new ActionDescriptor();
    fillDesc.putEnumerated(charIDToTypeID("Usng"), charIDToTypeID("FlCn"), stringIDToTypeID("contentAware"));
    fillDesc.putUnitDouble(charIDToTypeID("Opct"), charIDToTypeID("#Prc"), 100);
    fillDesc.putEnumerated(charIDToTypeID("Md  "), charIDToTypeID("BlnM"), charIDToTypeID("Nrml"));
    executeAction(charIDToTypeID("Fl  "), fillDesc, DialogModes.NO);
    doc.selection.deselect();
    return "ok";
})(arguments[0], arguments[1], arguments[2]);
