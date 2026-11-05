itch_require_gtest()
find_package(Python3 REQUIRED COMPONENTS Interpreter)

add_library(itch_test_support INTERFACE)
target_include_directories(itch_test_support INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(itch_test_support INTERFACE itch_parser)

# One GoogleTest executable, one ctest entry: itch_add_test(test_name test_name.cpp)
function(itch_add_test name)
  add_executable(${name} ${ARGN})
  target_link_libraries(${name} PRIVATE itch_test_support GTest::gtest_main)
  itch_apply_options(${name})
  add_test(NAME ${name} COMMAND ${name})
  set_tests_properties(${name} PROPERTIES LABELS unit)
endfunction()

itch_add_test(test_wire test_wire.cpp)
itch_add_test(test_messages test_messages.cpp)
itch_add_test(test_reader test_reader.cpp)
itch_add_test(test_guard_page test_guard_page.cpp)
itch_add_test(test_book test_book.cpp)
itch_add_test(test_alloc test_alloc.cpp oracle/alloc_counter.cpp)
itch_add_test(test_reference_files test_reference_files.cpp)
itch_add_test(test_trade_checks test_trade_checks.cpp)
itch_add_test(test_backtest test_backtest.cpp)
itch_add_test(test_fees test_fees.cpp)
itch_add_test(test_backtest_validity test_backtest_validity.cpp)
itch_add_test(test_attribution test_attribution.cpp)
itch_add_test(test_market_maker test_market_maker.cpp)
# A strategy that always sets a wake costs one event per wake period of simulated time, so a
# broken decoder's far-apart timestamps make these run for hours. A timeout makes that a
# failure. The slowest, test_backtest_validity under TSan, takes about 10 s.
set_tests_properties(test_backtest test_backtest_validity PROPERTIES TIMEOUT 60)
itch_add_test(test_bench_platform test_bench_platform.cpp)
itch_add_test(test_bench_replay test_bench_replay.cpp)
target_include_directories(test_bench_platform PRIVATE "${PROJECT_SOURCE_DIR}/bench")
target_include_directories(test_bench_replay PRIVATE "${PROJECT_SOURCE_DIR}/bench"
  "${PROJECT_SOURCE_DIR}/tools")

if(ITCH_BUILD_BENCH)
  add_test(NAME test_bench_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_bench_cli.py"
            "$<TARGET_FILE:bench_replay>" "$<TARGET_FILE:bench_probe>"
            "${CMAKE_CURRENT_BINARY_DIR}/bench_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle/session.itch")
  add_test(NAME test_bench_backtest_cli
    COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/test_bench_backtest_cli.py"
            "$<TARGET_FILE:bench_backtest>" "${CMAKE_CURRENT_BINARY_DIR}/bench_backtest_cli"
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/oracle/session.itch")
  set_tests_properties(test_bench_cli test_bench_backtest_cli PROPERTIES LABELS unit)
endif()

if(ITCH_BUILD_TOOLS)
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
            "${CMAKE_CURRENT_SOURCE_DIR}/fixtures/trades")
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
  set_tests_properties(test_census_cli test_oracle_cli test_trades_cli test_map_compare_cli
    test_backtest_cli test_queue_jumps_cli test_calibrate_cli test_select_cli
    PROPERTIES LABELS unit)
endif()
