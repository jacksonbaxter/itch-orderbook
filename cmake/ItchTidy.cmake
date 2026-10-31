# clang-tidy runs at one pinned major version, because new checks arrive with
# each release and an unpinned job would start failing by itself.

set(ITCH_CLANG_TIDY_MAJOR 23)

if(ITCH_TIDY)
  find_program(ITCH_CLANG_TIDY clang-tidy REQUIRED)
  execute_process(
    COMMAND "${ITCH_CLANG_TIDY}" --version
    OUTPUT_VARIABLE tidy_version_text
    COMMAND_ERROR_IS_FATAL ANY)
  if(NOT tidy_version_text MATCHES "version ([0-9]+)\\.")
    message(FATAL_ERROR "could not read a version from clang-tidy --version")
  endif()
  if(NOT CMAKE_MATCH_1 EQUAL ITCH_CLANG_TIDY_MAJOR)
    message(FATAL_ERROR
      "clang-tidy major version mismatch: found ${CMAKE_MATCH_1}, "
      "pinned ${ITCH_CLANG_TIDY_MAJOR}")
  endif()
  set(ITCH_CLANG_TIDY_COMMAND "${ITCH_CLANG_TIDY}" --warnings-as-errors=*)
endif()
