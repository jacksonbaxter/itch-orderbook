add_library(itch_parser STATIC parser.cpp)
target_include_directories(itch_parser PUBLIC "${PROJECT_SOURCE_DIR}/include")
if(ITCH_CACHE_LINE)
  target_compile_definitions(itch_parser PUBLIC "ITCH_CACHE_LINE=${ITCH_CACHE_LINE}")
endif()
itch_apply_options(itch_parser)

# The demo page's engine. Built natively as a library, so that clang-tidy and the native tests
# see the same file the page runs.
add_library(itch_demo_engine STATIC demo_engine.cpp)
target_include_directories(itch_demo_engine PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(itch_demo_engine PUBLIC itch_parser)
itch_apply_options(itch_demo_engine)

if(EMSCRIPTEN)
  add_executable(itch_wasm_smoke wasm_smoke.cpp)
  target_include_directories(itch_wasm_smoke PRIVATE "${PROJECT_SOURCE_DIR}/tests")
  target_link_libraries(itch_wasm_smoke PRIVATE itch_parser)
  itch_apply_options(itch_wasm_smoke)
  add_test(NAME itch_wasm_smoke COMMAND itch_wasm_smoke)

  # The page's engine, an ES module with its .wasm beside it: -O3 from the Release build,
  # 128-bit SIMD, and a linear memory of kEngineMemoryBytes (demo_engine.hpp) that never
  # grows, with malloc returning null rather than aborting when it is full.
  # scripts/demo.py build copies both files into docs/local/.
  set(itch_demo_memory
    -sINITIAL_MEMORY=1073741824 -sALLOW_MEMORY_GROWTH=0 -sABORTING_MALLOC=0 -sSTACK_SIZE=1048576)
  set(itch_demo_exports
    _demo_input _demo_load _demo_replay _demo_step _demo_messages _demo_applied _demo_status
    _demo_first_timestamp_ns _demo_last_timestamp_ns _demo_symbol _demo_depth
    _demo_depth_buffer _demo_prints _demo_print _demo_digest_high _demo_digest_low
    _demo_max_input_bytes _demo_memory_bytes)
  list(JOIN itch_demo_exports "," itch_demo_export_list)
  add_executable(itch_demo demo_engine.cpp)
  target_include_directories(itch_demo PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
  target_link_libraries(itch_demo PRIVATE itch_parser)
  target_compile_options(itch_demo PRIVATE -msimd128)
  target_link_options(itch_demo PRIVATE
    -msimd128 --no-entry ${itch_demo_memory}
    -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createItchDemo -sENVIRONMENT=web,node
    -sFILESYSTEM=0
    "-sEXPORTED_FUNCTIONS=${itch_demo_export_list}"
    "-sEXPORTED_RUNTIME_METHODS=HEAPU8,HEAPU32,HEAPF64,UTF8ToString")
  set_target_properties(itch_demo PROPERTIES SUFFIX ".mjs")
  itch_apply_options(itch_demo)
  # What built the engine, for engine.json and T-WASM-1's results header.
  string(TOUPPER "${CMAKE_BUILD_TYPE}" itch_demo_build_type)
  string(STRIP "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${itch_demo_build_type}} -std=c++23 -msimd128"
    itch_demo_flags)
  list(JOIN itch_demo_memory " " itch_demo_memory_text)
  file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/itch_demo_build.json" CONTENT
"{\"compiler\": \"em++, Emscripten ${EMSCRIPTEN_VERSION}, ${CMAKE_CXX_COMPILER_ID} \
${CMAKE_CXX_COMPILER_VERSION}\", \"flags\": \"${itch_demo_flags}; linked with \
${itch_demo_memory_text}\"}
")

  # The engine on the hand-built extract, in node, held to the native test's digest.
  add_executable(itch_demo_smoke demo_smoke.cpp demo_engine.cpp)
  target_include_directories(itch_demo_smoke PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}" "${PROJECT_SOURCE_DIR}/tests")
  target_link_libraries(itch_demo_smoke PRIVATE itch_parser)
  target_compile_options(itch_demo_smoke PRIVATE -msimd128)
  target_link_options(itch_demo_smoke PRIVATE -msimd128 ${itch_demo_memory})
  itch_apply_options(itch_demo_smoke)
  add_test(NAME itch_demo_smoke COMMAND itch_demo_smoke)
endif()
