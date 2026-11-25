// Sayso, compiled to WebAssembly, behind the same entry point the CLI uses.
//
// There is exactly one exported function and it calls `stage_result_json`, the
// same function `sayso normalize --json` calls. Nothing about the normaliser is
// reimplemented here and no part of the StageResult is assembled on the
// JavaScript side: the document the browser parses is byte for byte the
// document the native binary prints, which is what makes the parity test in
// `tests/test_wasm_parity.py` a real test rather than a tautology.
//
// The returned pointer is owned by this translation unit and is valid until the
// next call. The JavaScript wrapper copies it out immediately (`UTF8ToString`),
// so a single static buffer is enough and the caller never has to free.
#include <emscripten/emscripten.h>

#include <string>

#include "normalizer.h"
#include "report.h"

namespace {

// Held across calls so the pointer handed to JavaScript stays valid until the
// wrapper has copied it, and so one allocation serves every call.
std::string g_result;

sayso::Style parse_style(int style) {
    return style == 1 ? sayso::Style::Phone : sayso::Style::Plain;
}

sayso::DateSpeech parse_date_order(int date_order) {
    return date_order == 1 ? sayso::DateSpeech::PreserveWrittenOrder
                           : sayso::DateSpeech::Canonical;
}

}  // namespace

extern "C" {

// text         UTF-8, one utterance. A newline would be read as a second
//              sentence, so the wrapper folds vertical whitespace first,
//              exactly as the Python package does.
// style        0 plain, 1 phone
// date_order   0 canonical, 1 preserve
//
// Returns a pointer to a NUL-terminated UTF-8 JSON document, valid until the
// next call.
EMSCRIPTEN_KEEPALIVE
const char* sayso_stage_result(const char* text, int style, int date_order) {
    if (text == nullptr) text = "";
    g_result = sayso::stage_result_json(text, sayso::Direction::TTS,
                                        parse_date_order(date_order),
                                        parse_style(style));
    return g_result.c_str();
}

// The normalised text alone, for callers that want the one string and not the
// document. Used by the parity harness, which compares both.
EMSCRIPTEN_KEEPALIVE
const char* sayso_normalize(const char* text, int style, int date_order) {
    if (text == nullptr) text = "";
    g_result = sayso::normalize(text, sayso::Direction::TTS,
                                parse_date_order(date_order),
                                parse_style(style));
    return g_result.c_str();
}

}  // extern "C"
