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

add_executable(bench_counts bench_counts.cpp)
target_link_libraries(bench_counts PRIVATE itch_bench_support)
itch_apply_options(bench_counts)

add_executable(bench_variants bench_variants.cpp)
target_link_libraries(bench_variants PRIVATE itch_bench_support)
itch_apply_options(bench_variants)

add_executable(bench_chase bench_chase.cpp)
target_link_libraries(bench_chase PRIVATE itch_bench_support)
itch_apply_options(bench_chase)

add_executable(bench_backtest bench_backtest.cpp)
target_link_libraries(bench_backtest PRIVATE itch_bench_support)
itch_apply_options(bench_backtest)

# T-SPSC-1 and T-SPSC-2. Built in every configuration with the project ring and the mutex
# floor; the third-party queues join when ITCH_COMPARISON_QUEUES fetched them.
add_executable(bench_spsc bench_spsc.cpp)
target_link_libraries(bench_spsc PRIVATE itch_bench_support hdr_histogram::hdr_histogram_static)
if(ITCH_COMPARISON_QUEUES)
  target_link_libraries(bench_spsc PRIVATE itch::comparison_queues)
endif()
itch_apply_options(bench_spsc)

# T-SPSC-3: the pipelined replay against the single-threaded one, on a day.
add_executable(bench_pipeline bench_pipeline.cpp)
target_include_directories(bench_pipeline PRIVATE "${PROJECT_SOURCE_DIR}/tests")
target_link_libraries(bench_pipeline PRIVATE itch_bench_support)
itch_apply_options(bench_pipeline)
