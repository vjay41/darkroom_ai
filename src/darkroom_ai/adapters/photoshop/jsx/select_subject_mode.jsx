// Read-only: returns Photoshop's "Select Subject and Remove Background" processing mode
// (Preferences > Image Processing), e.g. "imageProcessingModeDevice".
(function () {
    var ref = new ActionReference();
    ref.putEnumerated(charIDToTypeID("capp"), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
    var prefs = executeActionGet(ref).getObjectValue(stringIDToTypeID("imageProcessingPrefs"));
    return typeIDToStringID(
        prefs.getEnumerationValue(stringIDToTypeID("imageProcessingSelectSubjectPrefs"))
    );
})();
