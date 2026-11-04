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
