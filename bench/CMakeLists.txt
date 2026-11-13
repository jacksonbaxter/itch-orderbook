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

# The walks of T-BT-3's simulated levels, counted through a copy of the engine made at build
# time with a count at each walk (cmake/ItchWalkEngine.cmake). The engine itself is untouched.
set(itch_walk_engine "${CMAKE_CURRENT_BINARY_DIR}/generated/walk_engine.hpp")
add_custom_command(OUTPUT "${itch_walk_engine}"
  COMMAND "${CMAKE_COMMAND}" "-DSOURCE=${PROJECT_SOURCE_DIR}/include/itch/backtest.hpp"
          "-DOUTPUT=${itch_walk_engine}" -P "${PROJECT_SOURCE_DIR}/cmake/ItchWalkEngine.cmake"
  DEPENDS "${PROJECT_SOURCE_DIR}/include/itch/backtest.hpp"
          "${PROJECT_SOURCE_DIR}/cmake/ItchWalkEngine.cmake"
  COMMENT "Copying the backtester's engine with its walks counted"
  VERBATIM)
add_custom_target(itch_walk_engine DEPENDS "${itch_walk_engine}")
add_executable(bench_walks bench_walks.cpp)
add_dependencies(bench_walks itch_walk_engine)
target_include_directories(bench_walks PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(bench_walks PRIVATE itch_bench_support)
itch_apply_options(bench_walks)

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

add_executable(bench_demo bench_demo.cpp)
target_link_libraries(bench_demo PRIVATE itch_bench_support)
itch_apply_options(bench_demo)

# T-ARENA-1's mimalloc arm in bench_replay, when the build fetched it.
if(TARGET itch::mimalloc)
  target_link_libraries(bench_replay PRIVATE itch::mimalloc)
endif()

# The SIMD experiments: every kernel this build has, chosen with --kernel, from one binary.
add_executable(bench_kernels bench_kernels.cpp)
target_link_libraries(bench_kernels PRIVATE itch_bench_support)
itch_apply_options(bench_kernels)

# T-URING-1 and T-URING-2: one run of the UDP replay on loopback, in any receive mode this
# build has (S1 everywhere, S2 to S4 on Linux, U1 to U5 with liburing).
add_executable(bench_udp bench_udp.cpp)
target_include_directories(bench_udp PRIVATE "${PROJECT_SOURCE_DIR}/tests")
target_link_libraries(bench_udp PRIVATE itch_bench_support hdr_histogram::hdr_histogram_static)
itch_udp_modes(bench_udp)
itch_apply_options(bench_udp)

# T-PARSE-1 to T-PARSE-3: the framing walk, itch-book's decode and the strict decode of every
# field, each over a day held in memory.
add_executable(bench_parse bench_parse.cpp)
target_link_libraries(bench_parse PRIVATE itch_bench_support)
itch_apply_options(bench_parse)
