// Runs Select Subject on the active document and saves the selection as a mask PNG
// (white = subject, black = everything else, soft edges kept).
// Argument: [0] absolute path of the PNG to write (must not exist).
//
// Offline rule: Select Subject is only run when Photoshop's processing mode is "Device". The mode
// is read here, in the same script, immediately before the call; any other value stops the script
// before Select Subject runs. The document is changed (helper channel and layer) and must be closed
// WITHOUT saving by the caller.
(function (outPath) {
    var DEVICE = "imageProcessingModeDevice";
    var appRef = new ActionReference();
    appRef.putEnumerated(charIDToTypeID("capp"), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
    var prefs = executeActionGet(appRef).getObjectValue(stringIDToTypeID("imageProcessingPrefs"));
    var mode = typeIDToStringID(
        prefs.getEnumerationValue(stringIDToTypeID("imageProcessingSelectSubjectPrefs"))
    );
    if (mode !== DEVICE) {
        throw new Error("SELECT_SUBJECT_NOT_DEVICE: mode is " + mode);
    }

    var out = new File(outPath);
    if (out.exists) {
        throw new Error("MASK_FILE_EXISTS: " + outPath);
    }

    var doc = app.activeDocument;
    doc.selection.deselect();

    var cutout = new ActionDescriptor();
    cutout.putBoolean(stringIDToTypeID("sampleAllLayers"), false);
    executeAction(stringIDToTypeID("autoCutout"), cutout, DialogModes.NO);

    var channel;
    try {
        channel = doc.channels.add();
        doc.selection.store(channel);
    } catch (e) {
        throw new Error("SELECT_SUBJECT_EMPTY: no subject was selected");
    }

    // Paint the selection white on a black layer; the picture then IS the mask.
    var black = new SolidColor();
    black.rgb.red = 0; black.rgb.green = 0; black.rgb.blue = 0;
    var white = new SolidColor();
    white.rgb.red = 255; white.rgb.green = 255; white.rgb.blue = 255;
    var layer = doc.artLayers.add();
    layer.name = "darkroom_ai mask";
    doc.activeLayer = layer;
    doc.selection.selectAll();
    doc.selection.fill(black);
    doc.selection.load(channel);
    doc.selection.fill(white);
    doc.selection.deselect();

    var options = new PNGSaveOptions();
    options.interlaced = false;
    doc.saveAs(out, options, true);  // true = save as a copy
    return mode;
})(arguments[0]);
