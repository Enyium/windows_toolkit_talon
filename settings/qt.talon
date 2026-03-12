user.ui_framework: Qt
-
settings():
    user.si_insert__yield_time = true
    user.si_insert__caret_still_ms = 55
    user.si_insert__caret_still_before_supp_char = true
    #i In Qt apps, sending Unicode supplementary characters (those consisting of two surrogates in UTF-16) *after* BMP characters most often leads to the supplementary characters being placed *before* all or some of the BMP characters. Yielding plus waiting for caret standstill remedies the problem.
