# One fuzz target: itch_add_fuzzer(fuzz_name). Its source is fuzz_name.cpp, and its
# seeds are fuzz/corpus/fuzz_name/, written by scripts/gen_fuzz_corpus.py.
#
# Under the fuzz preset it links libFuzzer and registers a short run, labelled
# fuzz-smoke. In every other build it links the standalone runner and ctest replays
# the seeds with no mutation.
set(ITCH_FUZZ_SMOKE_SECONDS 5 CACHE STRING "Seconds per target in the fuzz smoke run")

add_library(itch_fuzz_support INTERFACE)
target_include_directories(itch_fuzz_support INTERFACE
  "${CMAKE_CURRENT_SOURCE_DIR}" "${PROJECT_SOURCE_DIR}/tests")
target_link_libraries(itch_fuzz_support INTERFACE itch_parser)

function(itch_add_fuzzer name)
  set(corpus "${CMAKE_CURRENT_SOURCE_DIR}/corpus/${name}")
  if(ITCH_FUZZ)
    add_executable(${name} ${name}.cpp)
    target_link_options(${name} PRIVATE -fsanitize=fuzzer)
    # New inputs go to the scratch directory, which comes first. The committed seeds
    # are only read.
    set(scratch "${CMAKE_CURRENT_BINARY_DIR}/corpus/${name}")
    file(MAKE_DIRECTORY "${scratch}" "${CMAKE_CURRENT_BINARY_DIR}/artifacts")
    add_test(NAME fuzz_smoke_${name}
      COMMAND ${name} "-max_total_time=${ITCH_FUZZ_SMOKE_SECONDS}" -print_final_stats=1
              "-artifact_prefix=${CMAKE_CURRENT_BINARY_DIR}/artifacts/${name}-"
              "${scratch}" "${corpus}")
    set_tests_properties(fuzz_smoke_${name} PROPERTIES LABELS fuzz-smoke)
  else()
    add_executable(${name} ${name}.cpp standalone_runner.cpp)
    add_test(NAME corpus_${name} COMMAND ${name} "${corpus}")
    set_tests_properties(corpus_${name} PROPERTIES LABELS corpus)
  endif()
  target_link_libraries(${name} PRIVATE itch_fuzz_support)
  itch_apply_options(${name})
endfunction()

itch_add_fuzzer(fuzz_reader)
itch_add_fuzzer(fuzz_decode)
