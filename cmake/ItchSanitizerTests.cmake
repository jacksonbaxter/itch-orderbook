# The symbolizer of every sanitizer and fuzz test on macOS.
#
# A sanitizer report, and each NEW_FUNC line of libFuzzer, is symbolized by a child process.
# On macOS the default is the system symbolizer, which attaches to the process and so may
# wait on a system authorization dialog: with developer mode off, every report and every
# fuzz run stalls until someone answers it. An unattended check must never wait on a dialog.
# So each test of a sanitizer build on macOS runs with external_symbolizer_path set to the
# llvm-symbolizer beside the compiler, appended to ASAN_OPTIONS, UBSAN_OPTIONS and
# TSAN_OPTIONS after whatever the test preset set. Where there is no llvm-symbolizer there,
# the path is empty, which turns the external symbolizer off; the sanitizer's own in-process
# names remain. Linux is left as it is. scripts/test_sanitizer_tests.py holds this.
#
# Called once at the end of the top-level CMakeLists.txt, after every test is added:
#   itch_sanitizer_symbolizer(COMPILER path)

# The symbolizer to name for a compiler: the llvm-symbolizer in its directory, or empty.
function(itch_symbolizer_for compiler out)
  get_filename_component(dir "${compiler}" DIRECTORY)
  set(symbolizer "${dir}/llvm-symbolizer")
  if(dir STREQUAL "" OR NOT EXISTS "${symbolizer}" OR IS_DIRECTORY "${symbolizer}")
    set(symbolizer "")
  endif()
  set(${out} "${symbolizer}" PARENT_SCOPE)
endfunction()

# Every test of a directory and the directories under it.
function(itch_tests_under dir out)
  get_property(found DIRECTORY "${dir}" PROPERTY TESTS)
  set(pairs "")
  foreach(test IN LISTS found)
    list(APPEND pairs "${dir}|${test}")
  endforeach()
  get_property(subdirs DIRECTORY "${dir}" PROPERTY SUBDIRECTORIES)
  foreach(sub IN LISTS subdirs)
    itch_tests_under("${sub}" more)
    list(APPEND pairs ${more})
  endforeach()
  set(${out} "${pairs}" PARENT_SCOPE)
endfunction()

function(itch_sanitizer_symbolizer)
  cmake_parse_arguments(PARSE_ARGV 0 arg "" "COMPILER" "")
  itch_symbolizer_for("${arg_COMPILER}" symbolizer)
  set(changes "")
  foreach(variable ASAN_OPTIONS UBSAN_OPTIONS TSAN_OPTIONS)
    list(APPEND changes "${variable}=string_append::external_symbolizer_path=${symbolizer}")
  endforeach()
  itch_tests_under("${CMAKE_SOURCE_DIR}" pairs)
  foreach(pair IN LISTS pairs)
    string(REPLACE "|" ";" parts "${pair}")
    list(GET parts 0 dir)
    list(GET parts 1 test)
    set_property(TEST "${test}" DIRECTORY "${dir}" APPEND PROPERTY ENVIRONMENT_MODIFICATION
                 ${changes})
  endforeach()
  if(symbolizer STREQUAL "")
    message(STATUS "Sanitizer tests: no llvm-symbolizer beside the compiler, external symbolizer off")
  else()
    message(STATUS "Sanitizer tests: symbolized by ${symbolizer}")
  endif()
endfunction()
