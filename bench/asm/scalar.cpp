// E12: the scalar kernel, compiled for every target and flag set the audit asks for.
#include "itch/kernels/scalar.hpp"

#include "functions.hpp"

ITCH_E12_FUNCTIONS(e12_scalar, itch::kernels::scalar::Kernel, )
