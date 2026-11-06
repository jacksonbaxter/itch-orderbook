# E11 (simd.md): the Add Order decode on the scalar kernel against the SIMD128 kernel, in node,
# timed by scripts/simd_wasm_e11.mjs with performance.now(). Emscripten builds only. The day
# goes through a fixed 64 MiB window of linear memory a piece at a time, so the module never
# grows and never holds the day.
add_executable(itch_e11 e11.cpp)
target_link_libraries(itch_e11 PRIVATE itch_parser)
target_include_directories(itch_e11 PRIVATE "${PROJECT_SOURCE_DIR}/bench")
target_link_options(itch_e11 PRIVATE
  -msimd128 --no-entry -sINITIAL_MEMORY=134217728 -sALLOW_MEMORY_GROWTH=0
  -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createE11 -sENVIRONMENT=node -sFILESYSTEM=0
  "-sEXPORTED_FUNCTIONS=_e11_window,_e11_window_bytes,_e11_reset,_e11_pass,_e11_messages,_e11_items,_e11_fold_high,_e11_fold_low"
  "-sEXPORTED_RUNTIME_METHODS=HEAPU8")
set_target_properties(itch_e11 PROPERTIES SUFFIX ".mjs")
itch_apply_options(itch_e11)
string(TOUPPER "${CMAKE_BUILD_TYPE}" itch_e11_build_type)
string(STRIP "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${itch_e11_build_type}} -std=c++23 -msimd128"
  itch_e11_flags)
file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/itch_e11_build.json" CONTENT
"{\"compiler\": \"em++, Emscripten ${EMSCRIPTEN_VERSION}, ${CMAKE_CXX_COMPILER_ID} \
${CMAKE_CXX_COMPILER_VERSION}\", \"flags\": \"${itch_e11_flags}\"}
")
