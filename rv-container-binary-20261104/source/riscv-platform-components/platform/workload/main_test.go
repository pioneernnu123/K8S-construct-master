package main

import (
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPressureReleaseAndHealth(t *testing.T) {
	state := &loadState{bytes: 8192}
	state.update(true)
	if state.buffer[0] != 1 || state.buffer[4096] != 1 {
		t.Fatal("resident pages were not touched")
	}
	response := httptest.NewRecorder()
	state.handler().ServeHTTP(response, httptest.NewRequest("GET", "/health", nil))
	if !strings.Contains(response.Body.String(), `"pressure":true`) {
		t.Fatal(response.Body.String())
	}
	state.update(false)
	if state.active || state.buffer != nil {
		t.Fatal("pressure buffer retained after marker removal")
	}
}

func TestMarkerLifecycleAndBoundedConfiguration(t *testing.T) {
	path := filepath.Join(t.TempDir(), "marker")
	if err := marker(path, "on"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		if err := marker(path, "off"); err != nil {
			t.Fatal(err)
		}
	}
	if marker(path, "unknown") == nil || execute([]string{"serve", "--pressure-mib=193"}) == nil {
		t.Fatal("invalid marker/configuration accepted")
	}
}
