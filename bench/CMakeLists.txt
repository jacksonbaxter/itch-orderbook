itch_require_bench_deps()

# Flags and compiler as the build used them, for the results header.
string(TOUPPER "${CMAKE_BUILD_TYPE}" itch_build_type)
string(STRIP "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${itch_build_type}} -std=c++23" itch_bench_flags)

add_library(itch_bench_support INTERFACE)
target_include_directories(itch_bench_support INTERFACE
  "${CMAKE_CURRENT_SOURCE_DIR}" "${PROJECT_SOURCE_DIR}/tools")
target_compile_definitions(itch_bench_support INTERFACE
  "ITCH_BENCH_FLAGS=\"${itch_bench_flags}\"")
target_link_libraries(itch_bench_support INTERFACE itch_parser)

add_executable(bench_probe bench_probe.cpp)
target_link_libraries(bench_probe PRIVATE itch_bench_support)
itch_apply_options(bench_probe)

add_executable(bench_replay bench_replay.cpp)
target_link_libraries(bench_replay PRIVATE
  itch_bench_support benchmark::benchmark hdr_histogram::hdr_histogram_static)
itch_apply_options(bench_replay)
