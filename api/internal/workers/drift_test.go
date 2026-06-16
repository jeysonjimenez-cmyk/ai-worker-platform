package workers

import "testing"

// ─── T2.10 VRAM drift detection ───

func TestVRAMDriftExceeded(t *testing.T) {
	cases := []struct {
		name         string
		ledger       int
		reported     int
		margin       int
		wantExceeded bool
	}{
		{"within margin", 8000, 7600, 512, false},
		{"exact margin boundary", 8000, 7488, 512, false}, // diff=512, not strictly >
		{"one over margin", 8000, 7487, 512, true},        // diff=513 > 512
		{"reported higher within margin", 5000, 5400, 512, false},
		{"reported much higher", 5000, 6000, 512, true},
		{"zero margin zero diff", 5000, 5000, 0, false},
		{"zero margin any diff", 5000, 5001, 0, true},
		{"large drift", 16000, 0, 512, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := vramDriftExceeded(c.ledger, c.reported, c.margin)
			if got != c.wantExceeded {
				t.Errorf("vramDriftExceeded(ledger=%d, reported=%d, margin=%d) = %v, want %v",
					c.ledger, c.reported, c.margin, got, c.wantExceeded)
			}
		})
	}
}
