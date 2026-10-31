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
