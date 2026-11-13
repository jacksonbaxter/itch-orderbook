itch_require_gtest()
find_package(Python3 REQUIRED COMPONENTS Interpreter)

add_library(itch_test_support INTERFACE)
target_include_directories(itch_test_support INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(itch_test_support INTERFACE itch_parser)

# Every GoogleTest binary's main (support/gtest_main.cpp). Under ctest, which sets
# ITCH_CTEST_SKIPS, a binary that passed with a test skipped for a stated reason exits 77, which
# each entry takes as SKIP_RETURN_CODE: the entry shows as Skipped, never as Passed. A failure
# fails whatever skipped beside it, and so do a skip with no reason and a filter that matched
# no test. This is the one skip mechanism; a SKIP_REGULAR_EXPRESSION would take a failing
# binary that printed a skip as Skipped.
add_library(itch_gtest_main STATIC support/gtest_main.cpp)
target_link_libraries(itch_gtest_main PUBLIC GTest::gtest)
itch_apply_options(itch_gtest_main)

# One ctest entry running a GoogleTest binary: itch_gtest_entry(entry target [args...])
function(itch_gtest_entry entry target)
  add_test(NAME ${entry} COMMAND ${target} ${ARGN})
  set_tests_properties(${entry} PROPERTIES LABELS unit SKIP_RETURN_CODE 77
    ENVIRONMENT ITCH_CTEST_SKIPS=1)
endfunction()

# One GoogleTest executable and its ctest entry: itch_add_test(test_name test_name.cpp). Tests
# that can skip get entries of their own, each "SKIPS entry filter", so that a skip shows as
# that entry Skipped while the binary's other tests run, and show as passed, in the main entry:
#   itch_add_test(test_name test_name.cpp SKIPS test_name_x "Suite.Test")
function(itch_add_test name)
  cmake_parse_arguments(PARSE_ARGV 1 arg "" "" "SKIPS")
  add_executable(${name} ${arg_UNPARSED_ARGUMENTS})
  target_link_libraries(${name} PRIVATE itch_test_support itch_gtest_main)
  itch_apply_options(${name})
  set(skips ${arg_SKIPS})
  set(apart "")
  while(skips)
    list(POP_FRONT skips entry filter)
    itch_gtest_entry(${entry} ${name} "--gtest_filter=${filter}")
    list(APPEND apart "${filter}")
  endwhile()
  if(apart)
    list(JOIN apart ":" apart)
    itch_gtest_entry(${name} ${name} "--gtest_filter=-${apart}")
  else()
    itch_gtest_entry(${name} ${name})
  endif()
endfunction()

itch_add_test(test_wire test_wire.cpp)
itch_add_test(test_messages test_messages.cpp)
itch_add_test(test_reader test_reader.cpp)
itch_add_test(test_guard_page test_guard_page.cpp)
itch_add_test(test_book test_book.cpp)
itch_add_test(test_alloc test_alloc.cpp oracle/alloc_counter.cpp)
# T-ARENA-1's node book on every allocator arm, its pool and its pages.
itch_add_test(test_node_book test_node_book.cpp)
target_compile_definitions(test_node_book PRIVATE
  "ITCH_ORACLE_FIXTURES=\"${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle\"")
if(TARGET itch::mimalloc)
  target_link_libraries(test_node_book PRIVATE itch::mimalloc)
  target_link_libraries(test_alloc PRIVATE itch::mimalloc)
endif()
itch_add_test(test_reference_files test_reference_files.cpp)
itch_add_test(test_trade_checks test_trade_checks.cpp)
itch_add_test(test_backtest test_backtest.cpp)
itch_add_test(test_fees test_fees.cpp)
itch_add_test(test_backtest_validity test_backtest_validity.cpp)
itch_add_test(test_attribution test_attribution.cpp)
itch_add_test(test_market_maker test_market_maker.cpp)
itch_add_test(test_demo_replay test_demo_replay.cpp)
target_link_libraries(test_demo_replay PRIVATE itch_demo_engine)
# A strategy that always sets a wake costs one event per wake period of simulated time, so a
# broken decoder's far-apart timestamps make these run for hours. A timeout makes that a
# failure. The slowest, test_backtest_validity under TSan, takes about 10 s.
set_tests_properties(test_backtest test_backtest_validity PROPERTIES TIMEOUT 60)
# The ring's tests, and the stress tool that runs it without TSan. Both take the
# negative-control switch; nothing else does.
itch_add_test(test_spsc test_spsc.cpp
  SKIPS test_spsc_cache_line SpscRing.PaddingConstantIsTheHardwareLine)
add_executable(spsc_stress spsc_stress.cpp)
target_link_libraries(spsc_stress PRIVATE itch_test_support)
itch_apply_options(spsc_stress)
add_test(NAME spsc_stress_smoke COMMAND spsc_stress --rounds 4 --messages 50000)
set_tests_properties(spsc_stress_smoke PROPERTIES LABELS unit)
if(ITCH_SPSC_NEGATIVE_CONTROL)
  if(NOT ITCH_SPSC_NEGATIVE_CONTROL MATCHES "^(publish|observe|return|reclaim)$")
    message(FATAL_ERROR "ITCH_SPSC_NEGATIVE_CONTROL is publish, observe, return or reclaim")
  endif()
  string(TOUPPER "${ITCH_SPSC_NEGATIVE_CONTROL}" itch_weakened)
  target_compile_definitions(test_spsc PRIVATE "ITCH_SPSC_WEAKEN_${itch_weakened}")
  target_compile_definitions(spsc_stress PRIVATE "ITCH_SPSC_WEAKEN_${itch_weakened}")
  message(STATUS "SPSC negative control: ${ITCH_SPSC_NEGATIVE_CONTROL} weakened to relaxed")
endif()
itch_add_test(test_pipeline test_pipeline.cpp)
target_include_directories(test_pipeline PRIVATE "${PROJECT_SOURCE_DIR}/bench")
target_include_directories(test_alloc PRIVATE "${PROJECT_SOURCE_DIR}/bench")
# The cross-core clock test skips under a hypervisor, as on the shared CI runners.
itch_add_test(test_bench_platform test_bench_platform.cpp
  SKIPS test_bench_platform_cross_core BenchCrossCore.TheLiveClocksAgreeAcrossTwoThreads)
itch_add_test(test_bench_replay test_bench_replay.cpp)
target_include_directories(test_bench_platform PRIVATE "${PROJECT_SOURCE_DIR}/bench")
target_include_directories(test_bench_replay PRIVATE "${PROJECT_SOURCE_DIR}/bench"
  "${PROJECT_SOURCE_DIR}/tools")
if(TARGET itch::mimalloc)
  target_link_libraries(test_bench_replay PRIVATE itch::mimalloc)
endif()
itch_add_test(test_book_variants test_book_variants.cpp)
target_include_directories(test_book_variants PRIVATE "${PROJECT_SOURCE_DIR}/bench")

# MoldUDP64 framing, the sequence check, the prebuilt packets and the feed (T-URING-1).
itch_add_test(test_mold test_mold.cpp)
target_compile_definitions(test_mold PRIVATE
  "ITCH_ORACLE_FIXTURES=\"${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle\""
  "ITCH_TRADE_FIXTURES=\"${CMAKE_CURRENT_SOURCE_DIR}/fixtures/trades\"")
# The replay sender's pacing on a hand-moved clock, S1, and the two on loopback.
# The slot modes are Linux's, the multishot io_uring modes need liburing: with none built,
# their tests skip.
itch_add_test(test_udp test_udp.cpp
  SKIPS test_udp_slot_modes Modes.ADatagramPastTheSlotIsCountedTruncated
        test_udp_ring_modes
        "Modes.AMultishotModeOutOfBuffersRearmsAndLosesNothing:Modes.ARingModeWithABadBufferCountFailsItsSetup")
target_include_directories(test_udp PRIVATE "${PROJECT_SOURCE_DIR}/bench")
itch_udp_modes(test_udp)
itch_udp_modes(test_alloc)
target_compile_definitions(test_udp PRIVATE
  "ITCH_ORACLE_FIXTURES=\"${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle\"")

# The parsing kernels. Each kernel this build has is its own ctest entry, so that one the CPU
# cannot run shows as skipped, never as passed, and an entry whose filter matches nothing
# fails. test_kernels runs the rest: selection, the parser's scalar tail, the scalar oracle.
add_executable(test_kernels test_kernels.cpp)
target_link_libraries(test_kernels PRIVATE itch_test_support itch_gtest_main)
list(JOIN ITCH_KERNELS ", " itch_expected_kernels)
target_compile_definitions(test_kernels PRIVATE
  "ITCH_ORACLE_FIXTURES=\"${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle\""
  "ITCH_EXPECTED_KERNELS=\"${itch_expected_kernels}\"")
itch_apply_options(test_kernels)
itch_gtest_entry(test_kernels test_kernels --gtest_filter=Kernels.*)
foreach(kernel ${ITCH_KERNELS})
  itch_gtest_entry(test_kernels_${kernel} test_kernels "--gtest_filter=Built/KernelTest.*/${kernel}")
  set_tests_properties(test_kernels_${kernel} PROPERTIES FAIL_REGULAR_EXPRESSION "Running 0 tests")
endforeach()

# The receive modes itch_udp_modes() gives this build, for the tools' CLI tests.
set(itch_udp_modes_built S1)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  list(APPEND itch_udp_modes_built S2 S3 S4)
endif()
if(TARGET liburing::liburing)
  list(APPEND itch_udp_modes_built U1 U2 U3 U4 U5)
endif()
list(JOIN itch_udp_modes_built "," itch_udp_modes_built)

# The skip mechanism itself: the probe's passes, skips and failures through the main, every
# entry's skip properties, the tests that skip in entries of their own, and the receive mode
# entries skipping exactly when none of their modes is built.
add_executable(skip_probe skip_probe.cpp)
target_link_libraries(skip_probe PRIVATE itch_gtest_main)
itch_apply_options(skip_probe)
add_test(NAME test_skips_cli
  COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_skips_cli.py"
          "$<TARGET_FILE:skip_probe>" "${PROJECT_BINARY_DIR}" "${itch_udp_modes_built}")
set_tests_properties(test_skips_cli PROPERTIES LABELS unit)

if(ITCH_BUILD_BENCH)
  # bench_walks' counted engine, made in bench/ (cmake/ItchWalkEngine.cmake), against hand
  # counts and against the engine.
  itch_add_test(test_walk_counts test_walk_counts.cpp)
  add_dependencies(test_walk_counts itch_walk_engine)
  target_include_directories(test_walk_counts PRIVATE "${PROJECT_SOURCE_DIR}/bench"
    "${PROJECT_BINARY_DIR}/bench/generated")
  set_tests_properties(test_walk_counts PROPERTIES TIMEOUT 60)
  add_test(NAME test_bench_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_bench_cli.py"
            "$<TARGET_FILE:bench_replay>" "$<TARGET_FILE:bench_probe>"
            "${CMAKE_CURRENT_BINARY_DIR}/bench_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle/session.itch")
  add_test(NAME test_bench_backtest_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_bench_backtest_cli.py"
            "$<TARGET_FILE:bench_backtest>" "${CMAKE_CURRENT_BINARY_DIR}/bench_backtest_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle/session.itch")
  add_test(NAME test_bench_counts_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_bench_counts_cli.py"
            "$<TARGET_FILE:bench_counts>" "${CMAKE_CURRENT_BINARY_DIR}/bench_counts_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle/session.itch")
  add_test(NAME test_bench_variants_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_bench_variants_cli.py"
            "$<TARGET_FILE:bench_variants>" "${CMAKE_CURRENT_BINARY_DIR}/bench_variants_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle/session.itch")
  add_test(NAME test_bench_spsc_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_bench_spsc_cli.py"
            "$<TARGET_FILE:bench_spsc>" "$<TARGET_FILE:bench_pipeline>"
            "${CMAKE_CURRENT_BINARY_DIR}/bench_spsc_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle/session.itch")
  add_test(NAME test_bench_kernels_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_bench_kernels_cli.py"
            "$<TARGET_FILE:bench_kernels>" "${CMAKE_CURRENT_BINARY_DIR}/bench_kernels_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle/session.itch")
  add_test(NAME test_bench_udp_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_bench_udp_cli.py"
            "$<TARGET_FILE:bench_udp>" "${CMAKE_CURRENT_BINARY_DIR}/bench_udp_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle/session.itch" "${itch_udp_modes_built}")
  add_test(NAME test_bench_walks_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_bench_walks_cli.py"
            "$<TARGET_FILE:bench_walks>" "${CMAKE_CURRENT_BINARY_DIR}/bench_walks_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle/session.itch")
  set_tests_properties(test_bench_cli test_bench_backtest_cli test_bench_counts_cli
    test_bench_variants_cli test_bench_spsc_cli test_bench_kernels_cli test_bench_udp_cli
    test_bench_walks_cli PROPERTIES LABELS unit)
endif()

if(ITCH_BUILD_TOOLS)
  # Whether the tools that run the node book have its mimalloc arm, so that a CLI test can
  # tell a build without it from a tool that has lost it.
  if(TARGET itch::mimalloc)
    set(itch_mimalloc_built with-mimalloc)
  else()
    set(itch_mimalloc_built without-mimalloc)
  endif()
  add_test(NAME test_census_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_census_cli.py"
            "$<TARGET_FILE:itch_census>" "${CMAKE_CURRENT_BINARY_DIR}/census_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/support/fixtures.hpp")
  add_test(NAME test_oracle_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_oracle_cli.py"
            "$<TARGET_FILE:itch_oracle>" "${CMAKE_CURRENT_BINARY_DIR}/oracle_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle" "$<TARGET_FILE:itch_alloc>")
  add_test(NAME test_trades_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_trades_cli.py"
            "$<TARGET_FILE:itch_trades>" "${CMAKE_CURRENT_BINARY_DIR}/trades_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/trades")
  add_test(NAME test_map_compare_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_map_compare_cli.py"
            "$<TARGET_FILE:itch_map_compare>" "${CMAKE_CURRENT_BINARY_DIR}/map_compare_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/trades" "${itch_mimalloc_built}")
  add_test(NAME test_backtest_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_backtest_cli.py"
            "$<TARGET_FILE:itch_backtest>" "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle")
  add_test(NAME test_queue_jumps_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_queue_jumps_cli.py"
            "$<TARGET_FILE:itch_queue_jumps>" "${CMAKE_CURRENT_BINARY_DIR}/queue_jumps_cli")
  add_test(NAME test_calibrate_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_calibrate_cli.py"
            "$<TARGET_FILE:itch_calibrate>" "${CMAKE_CURRENT_BINARY_DIR}/calibrate_cli")
  add_test(NAME test_select_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_select_cli.py"
            "$<TARGET_FILE:itch_select>" "${CMAKE_CURRENT_BINARY_DIR}/select_cli")
  add_test(NAME test_mm_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_mm_cli.py"
            "$<TARGET_FILE:itch_mm>" "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle")
  add_test(NAME test_loopback_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_loopback_cli.py"
            "$<TARGET_FILE:itch_loopback>" "${CMAKE_CURRENT_BINARY_DIR}/loopback_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle" "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/trades"
            "${itch_udp_modes_built}")
  set_tests_properties(test_census_cli test_oracle_cli test_trades_cli test_map_compare_cli
    test_backtest_cli test_queue_jumps_cli test_calibrate_cli test_select_cli test_mm_cli
    test_loopback_cli PROPERTIES LABELS unit)
endif()

if(ITCH_BUILD_TOOLS AND ITCH_BUILD_BENCH)
  add_test(NAME test_demo_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_demo_cli.py"
            "$<TARGET_FILE:itch_extract>" "$<TARGET_FILE:bench_demo>"
            "${CMAKE_CURRENT_BINARY_DIR}/demo_cli")
  set_tests_properties(test_demo_cli PROPERTIES LABELS unit)
endif()
