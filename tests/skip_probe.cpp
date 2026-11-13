// Tests that pass, skip and fail on purpose, for test_skips_cli.py to run through the main every
// GoogleTest binary here has (support/gtest_main.cpp), a filter at a time. No ctest entry
// runs this binary on its own: Probe.Fails fails.
#include <gtest/gtest.h>

namespace {

TEST(Probe, Passes) { SUCCEED(); }

TEST(Probe, SkipsWithAReason) { GTEST_SKIP() << "the probe's reason"; }

TEST(Probe, SkipsWithNoReason) { GTEST_SKIP(); }

TEST(Probe, Fails) { ADD_FAILURE() << "the probe fails"; }

}  // namespace
