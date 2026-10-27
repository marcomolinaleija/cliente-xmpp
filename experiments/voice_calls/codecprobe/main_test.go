package main

import (
	"math"
	"testing"
)

func TestComparisonDistinguishesExactSamplesFromSignalError(t *testing.T) {
	a := []float64{0, .5, -.5}
	exact := compareSignal(a, a)
	if exact.RMSE != 0 || exact.ExactPCM16Samples != 3 || math.Abs(exact.Correlation-1) > 1e-10 {
		t.Fatal("bad exact comparison")
	}
	different := compareSignal(a, []float64{0, -.5, .5})
	if different.RMSE == 0 || different.Correlation >= 0 || different.ExactPCM16Samples != 1 {
		t.Fatal("comparison hid a mismatch")
	}
}
