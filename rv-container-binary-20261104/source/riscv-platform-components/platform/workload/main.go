// Standalone test workload for the outline's HPA and scheduling cases.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"runtime/debug"
	"sync"
	"syscall"
	"time"
)

const markerPath = "/tmp/enable_pressure"

type loadState struct {
	mu     sync.RWMutex
	buffer []byte
	active bool
	bytes  int
}

func (s *loadState) update(enabled bool) {
	s.mu.Lock()
	wasActive := s.active
	if enabled {
		if s.buffer == nil {
			s.buffer = make([]byte, s.bytes)
		}
		// Touch every page so the workload creates resident memory pressure.
		for i := 0; i < len(s.buffer); i += os.Getpagesize() {
			s.buffer[i]++
		}
	} else {
		s.buffer = nil
	}
	s.active = enabled
	s.mu.Unlock()
	if wasActive && !enabled {
		// A retained allocator heap would defeat the HPA shrink experiment.
		debug.FreeOSMemory()
	}
	runtime.KeepAlive(s)
}

func marker(path, value string) error {
	switch value {
	case "on":
		return os.WriteFile(path, []byte("enabled\n"), 0600)
	case "off":
		err := os.Remove(path)
		if os.IsNotExist(err) {
			return nil
		}
		return err
	default:
		return fmt.Errorf("marker value must be on or off")
	}
}

func (s *loadState) snapshot() map[string]interface{} {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return map[string]interface{}{"pressure": s.active, "residentBufferBytes": len(s.buffer)}
}

func (s *loadState) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(s.snapshot())
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		s.mu.RLock()
		defer s.mu.RUnlock()
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		fmt.Fprintf(w, "rv_test_resident_buffer_bytes %d\n", len(s.buffer))
	})
	return mux
}

func execute(args []string) error {
	if len(args) == 2 && args[0] == "marker" {
		return marker(markerPath, args[1])
	}
	if len(args) == 0 || (args[0] != "serve" && args[0] != "hold") {
		return fmt.Errorf("usage: rv-test-workload serve|hold [--listen=:8080] [--pressure-mib=160] | marker on|off")
	}
	flags := flag.NewFlagSet(args[0], flag.ContinueOnError)
	listen := flags.String("listen", ":8080", "health/metrics HTTP listen address")
	pressure := flags.Int("pressure-mib", 160, "memory pressure in MiB, range 1..192")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if flags.NArg() != 0 || *pressure < 1 || *pressure > 192 {
		return fmt.Errorf("unexpected arguments or pressure outside 1..192 MiB")
	}
	state := &loadState{bytes: *pressure * 1024 * 1024}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(stop)
	errors := make(chan error, 1)
	server := &http.Server{Addr: *listen, Handler: state.handler(), ReadHeaderTimeout: 5 * time.Second}
	go func() { errors <- server.ListenAndServe() }()
	defer server.Close()
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-stop:
			return nil
		case err := <-errors:
			return err
		case <-ticker.C:
			_, err := os.Stat(markerPath)
			if err != nil && !os.IsNotExist(err) {
				return err
			}
			state.update(args[0] == "serve" && err == nil)
		}
	}
}

func main() {
	if err := execute(os.Args[1:]); err != nil {
		log.Fatal(err)
	}
}
