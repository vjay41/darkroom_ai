// Turns the bottom layer into a normal layer and gives it a layer mask taken from a grayscale
// PNG of the same pixel size (white = keep, black = hide). Does not use the clipboard.
// Arguments: [0] absolute path of the mask PNG, [1] name for the masked layer.
(function (maskPath, layerName) {
    var doc = app.activeDocument;
    var layer = doc.layers[doc.layers.length - 1];
    if (layer.isBackgroundLayer) {
        layer.isBackgroundLayer = false;
    }
    layer.name = layerName;

    var maskDoc = app.open(new File(maskPath));
    var sameSize = Math.round(maskDoc.width.as("px")) === Math.round(doc.width.as("px")) &&
        Math.round(maskDoc.height.as("px")) === Math.round(doc.height.as("px"));
    if (!sameSize) {
        maskDoc.close(SaveOptions.DONOTSAVECHANGES);
        throw new Error("The mask is not the same pixel size as the document.");
    }
    // Copy the mask picture into the document as a temporary top layer.
    var temp = maskDoc.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
    maskDoc.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = doc;

    // Show only the temporary layer and load its brightness as the selection.
    var visibility = [];
    for (var i = 0; i < doc.layers.length; i++) {
        visibility.push(doc.layers[i].visible);
        doc.layers[i].visible = (doc.layers[i] == temp);
    }
    var selectDesc = new ActionDescriptor();
    var selectionRef = new ActionReference();
    selectionRef.putProperty(charIDToTypeID("Chnl"), charIDToTypeID("fsel"));
    selectDesc.putReference(charIDToTypeID("null"), selectionRef);
    var compositeRef = new ActionReference();
    compositeRef.putEnumerated(charIDToTypeID("Chnl"), charIDToTypeID("Chnl"), charIDToTypeID("RGB "));
    selectDesc.putReference(charIDToTypeID("T   "), compositeRef);
    executeAction(charIDToTypeID("setd"), selectDesc, DialogModes.NO);

    // Remove the temporary layer and restore visibility.
    temp.remove();
    for (var j = 0; j < doc.layers.length; j++) {
        doc.layers[j].visible = visibility[j + 1];
    }
    layer.visible = true;
    doc.activeLayer = layer;

    // Add a layer mask that reveals the selection.
    var maskDesc = new ActionDescriptor();
    maskDesc.putClass(charIDToTypeID("Nw  "), charIDToTypeID("Chnl"));
    var maskRef = new ActionReference();
    maskRef.putEnumerated(charIDToTypeID("Chnl"), charIDToTypeID("Chnl"), charIDToTypeID("Msk "));
    maskDesc.putReference(charIDToTypeID("At  "), maskRef);
    maskDesc.putEnumerated(charIDToTypeID("Usng"), charIDToTypeID("UsrM"), charIDToTypeID("RvlS"));
    executeAction(charIDToTypeID("Mk  "), maskDesc, DialogModes.NO);
    return "ok";
})(arguments[0], arguments[1]);
