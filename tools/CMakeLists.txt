add_executable(itch_census itch_census.cpp)
target_link_libraries(itch_census PRIVATE itch_parser)
itch_apply_options(itch_census)

add_executable(itch_oracle itch_oracle.cpp)
target_include_directories(itch_oracle PRIVATE "${PROJECT_SOURCE_DIR}/tests")
target_link_libraries(itch_oracle PRIVATE itch_parser)
itch_apply_options(itch_oracle)

add_executable(itch_alloc itch_alloc.cpp "${PROJECT_SOURCE_DIR}/tests/oracle/alloc_counter.cpp")
target_include_directories(itch_alloc PRIVATE "${PROJECT_SOURCE_DIR}/tests")
target_link_libraries(itch_alloc PRIVATE itch_parser)
itch_apply_options(itch_alloc)

add_executable(itch_trades itch_trades.cpp)
target_include_directories(itch_trades PRIVATE "${PROJECT_SOURCE_DIR}/tests")
target_link_libraries(itch_trades PRIVATE itch_parser)
itch_apply_options(itch_trades)

add_executable(itch_map_compare itch_map_compare.cpp)
target_include_directories(itch_map_compare PRIVATE "${PROJECT_SOURCE_DIR}/tests")
target_link_libraries(itch_map_compare PRIVATE itch_parser)
itch_apply_options(itch_map_compare)

add_executable(itch_backtest itch_backtest.cpp "${PROJECT_SOURCE_DIR}/tests/oracle/alloc_counter.cpp")
target_include_directories(itch_backtest PRIVATE "${PROJECT_SOURCE_DIR}/tests")
target_link_libraries(itch_backtest PRIVATE itch_parser)
itch_apply_options(itch_backtest)

add_executable(itch_queue_jumps itch_queue_jumps.cpp)
target_link_libraries(itch_queue_jumps PRIVATE itch_parser)
itch_apply_options(itch_queue_jumps)

add_executable(itch_as_tables itch_as_tables.cpp)
target_link_libraries(itch_as_tables PRIVATE itch_parser)
itch_apply_options(itch_as_tables)

add_executable(itch_calibrate itch_calibrate.cpp)
target_link_libraries(itch_calibrate PRIVATE itch_parser)
itch_apply_options(itch_calibrate)

add_executable(itch_select itch_select.cpp)
target_link_libraries(itch_select PRIVATE itch_parser)
itch_apply_options(itch_select)

add_executable(itch_mm itch_mm.cpp "${PROJECT_SOURCE_DIR}/tests/oracle/alloc_counter.cpp")
target_include_directories(itch_mm PRIVATE "${PROJECT_SOURCE_DIR}/tests")
target_link_libraries(itch_mm PRIVATE itch_parser)
itch_apply_options(itch_mm)

add_executable(itch_extract itch_extract.cpp)
target_link_libraries(itch_extract PRIVATE itch_demo_engine)
itch_apply_options(itch_extract)

# T-ARENA-1's mimalloc arm, in the tools that run the node book, when the build fetched it.
if(TARGET itch::mimalloc)
  target_link_libraries(itch_alloc PRIVATE itch::mimalloc)
  target_link_libraries(itch_map_compare PRIVATE itch::mimalloc)
endif()

add_executable(itch_window itch_window.cpp)
target_link_libraries(itch_window PRIVATE itch_parser)
itch_apply_options(itch_window)

# The UDP replay's functional check on loopback: the sender and any receive mode this build
# has from bench/udp, the book digest from the oracle's canonical walk. Never a measurement.
add_executable(itch_loopback itch_loopback.cpp)
target_include_directories(itch_loopback PRIVATE
  "${PROJECT_SOURCE_DIR}/bench" "${PROJECT_SOURCE_DIR}/tests")
target_link_libraries(itch_loopback PRIVATE itch_parser)
itch_udp_modes(itch_loopback)
itch_apply_options(itch_loopback)
