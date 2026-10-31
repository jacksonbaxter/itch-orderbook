cmake_minimum_required(VERSION 3.28)

project(itch_orderbook VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)

option(ITCH_BUILD_TESTS "Build the ctest suite" ON)
option(ITCH_TIDY "Run clang-tidy on our targets while compiling" OFF)
set(ITCH_SANITIZE "" CACHE STRING "Sanitizers, comma separated, for example address,undefined")

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(ItchOptions)
include(ItchTidy)
include(ItchDependencies)

enable_testing()

add_subdirectory(src)
if(ITCH_BUILD_TESTS)
  add_subdirectory(tests)
endif()
