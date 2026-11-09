package monitor

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

type Config struct {
	Targets             []string `json:"targets"`
	ScrapeInterval      string   `json:"scrapeInterval"`
	ScrapeTimeout       string   `json:"scrapeTimeout"`
	Retention           string   `json:"retention"`
	Lookback            string   `json:"lookback"`
	MaxSeries           int      `json:"maxSeries"`
	MaxSamplesPerScrape int      `json:"maxSamplesPerScrape"`
	MaxBodyBytes        int64    `json:"maxBodyBytes"`
}

type Target struct {
	ScrapeURL  string            `json:"scrapeUrl"`
	Labels     map[string]string `json:"labels"`
	Health     string            `json:"health"`
	LastScrape string            `json:"lastScrape"`
	LastError  string            `json:"lastError"`
}

type Server struct {
	Store                       *Store
	config                      Config
	interval, timeout, lookback time.Duration
	client                      *http.Client
	mu                          sync.RWMutex
	targets                     []Target
}

func New(c Config) (*Server, error) {
	defaults := map[*string]string{&c.ScrapeInterval: "10s", &c.ScrapeTimeout: "5s", &c.Retention: "15m", &c.Lookback: "45s"}
	for field, value := range defaults {
		if *field == "" {
			*field = value
		}
	}
	durations := make([]time.Duration, 4)
	for i, v := range []string{c.ScrapeInterval, c.ScrapeTimeout, c.Retention, c.Lookback} {
		d, err := time.ParseDuration(v)
		if err != nil || d <= 0 {
			return nil, fmt.Errorf("invalid positive duration %q", v)
		}
		durations[i] = d
	}
	if durations[0] < time.Second || durations[1] > durations[0] || durations[2] < durations[3] {
		return nil, fmt.Errorf("require interval>=1s, timeout<=interval, retention>=lookback")
	}
	if len(c.Targets) == 0 || len(c.Targets) > 1000 {
		return nil, fmt.Errorf("configure 1..1000 targets")
	}
	if c.MaxSeries == 0 {
		c.MaxSeries = 10000
	}
	if c.MaxSamplesPerScrape == 0 {
		c.MaxSamplesPerScrape = 1000
	}
	if c.MaxBodyBytes == 0 {
		c.MaxBodyBytes = 2 << 20
	}
	if c.MaxSeries < 1 || c.MaxSamplesPerScrape < 1 || c.MaxBodyBytes < 1 {
		return nil, fmt.Errorf("limits must be positive")
	}
	s := &Server{Store: NewStore(durations[2], c.MaxSeries), config: c, interval: durations[0], timeout: durations[1], lookback: durations[3]}
	s.client = &http.Client{Timeout: s.timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	seen := make(map[string]bool)
	for _, value := range c.Targets {
		if !strings.Contains(value, "://") {
			value = "http://" + value + "/metrics"
		}
		u, err := url.Parse(value)
		if err != nil || u.Host == "" || u.User != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Fragment != "" {
			return nil, fmt.Errorf("invalid target %q", value)
		}
		if u.Path == "" {
			u.Path = "/metrics"
		}
		value = u.String()
		if seen[value] {
			return nil, fmt.Errorf("duplicate target %q", value)
		}
		seen[value] = true
		s.targets = append(s.targets, Target{ScrapeURL: value, Labels: map[string]string{"job": "rv-nodes", "instance": u.Host}, Health: "unknown"})
	}
	return s, nil
}

func (s *Server) scrape(ctx context.Context, index int) {
	s.mu.RLock()
	target := s.targets[index]
	s.mu.RUnlock()
	ctx, cancel := context.WithTimeout(ctx, s.timeout)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target.ScrapeURL, nil)
	if err != nil {
		return
	}
	request.Header.Set("Accept", "application/openmetrics-text; version=1.0.0, text/plain; version=0.0.4; q=0.8")
	response, err := s.client.Do(request)
	var samples []Sample
	at := time.Now()
	if err == nil {
		func() {
			defer response.Body.Close()
			if response.StatusCode != 200 {
				err = fmt.Errorf("scrape HTTP %d", response.StatusCode)
				return
			}
			var body []byte
			body, err = io.ReadAll(io.LimitReader(response.Body, s.config.MaxBodyBytes+1))
			if err != nil {
				return
			}
			if int64(len(body)) > s.config.MaxBodyBytes {
				err = fmt.Errorf("scrape body limit exceeded")
				return
			}
			samples, err = Parse(body, response.Header.Get("Content-Type"), target.Labels["instance"], at, s.config.MaxSamplesPerScrape)
		}()
	}
	if err == nil {
		samples = append(samples, Sample{Labels: map[string]string{"__name__": "up", "job": "rv-nodes", "instance": target.Labels["instance"]}, Time: at.UnixMilli(), Value: 1})
		err = s.Store.Apply(target.ScrapeURL, samples, at)
	}
	s.mu.Lock()
	s.targets[index].LastScrape = at.UTC().Format(time.RFC3339Nano)
	if err != nil {
		s.targets[index].Health = "down"
		s.targets[index].LastError = err.Error()
		s.Store.Fail(target.ScrapeURL, at)
		log.Printf("scrape %s: %v", target.ScrapeURL, err)
	} else {
		s.targets[index].Health = "up"
		s.targets[index].LastError = ""
	}
	s.mu.Unlock()
}

func (s *Server) Run(ctx context.Context) {
	var workers sync.WaitGroup
	for i := range s.targets {
		workers.Add(1)
		go func(index int) {
			defer workers.Done()
			ticker := time.NewTicker(s.interval)
			defer ticker.Stop()
			for {
				if ctx.Err() != nil {
					return
				}
				s.scrape(ctx, index)
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
				}
			}
		}(i)
	}
	<-ctx.Done()
	workers.Wait()
}

func respond(w http.ResponseWriter, code int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(body); err != nil {
		log.Printf("response: %v", err)
	}
}

func apiError(w http.ResponseWriter, code int, message string) {
	respond(w, code, map[string]any{"status": "error", "errorType": "bad_data", "error": message})
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/-/healthy", func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("healthy\n")) })
	mux.HandleFunc("/-/ready", func(w http.ResponseWriter, r *http.Request) {
		s.mu.RLock()
		defer s.mu.RUnlock()
		for _, t := range s.targets {
			if t.Health == "up" {
				w.Write([]byte("ready\n"))
				return
			}
		}
		http.Error(w, "waiting for a successful scrape", http.StatusServiceUnavailable)
	})
	mux.HandleFunc("/api/v1/targets", func(w http.ResponseWriter, r *http.Request) {
		s.mu.RLock()
		defer s.mu.RUnlock()
		respond(w, 200, map[string]any{"status": "success", "data": map[string]any{"activeTargets": s.targets, "droppedTargets": []any{}}})
	})
	mux.HandleFunc("/api/v1/query", s.queryHandler)
	return mux
}

func (s *Server) queryHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		w.Header().Set("Allow", "GET, POST")
		apiError(w, 405, "method not allowed")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
	if err := r.ParseForm(); err != nil {
		apiError(w, 400, err.Error())
		return
	}
	query := r.Form.Get("query")
	if len(query) > 8192 {
		apiError(w, 400, "query too long")
		return
	}
	at := time.Now()
	historical := false
	if stamp := r.Form.Get("time"); stamp != "" {
		v, err := strconv.ParseFloat(stamp, 64)
		if err == nil {
			if !isFiniteTime(v) {
				apiError(w, 400, "invalid query time")
				return
			}
			at = time.UnixMilli(int64(v * 1000))
		} else {
			at, err = time.Parse(time.RFC3339Nano, stamp)
			if err != nil {
				apiError(w, 400, "invalid query time")
				return
			}
		}
		if at.After(time.Now().Add(time.Second)) {
			apiError(w, 400, "query time is in the future")
			return
		}
		historical = true
	}
	vector, err := s.Store.Query(query, at, s.lookback, historical)
	if err != nil {
		apiError(w, 422, err.Error())
		return
	}
	result := make([]any, 0, len(vector))
	for _, sample := range vector {
		result = append(result, map[string]any{"metric": sample.Labels, "value": []any{float64(sample.Time) / 1000, strconv.FormatFloat(sample.Value, 'g', -1, 64)}})
	}
	respond(w, 200, map[string]any{"status": "success", "data": map[string]any{"resultType": "vector", "result": result}})
}

func isFiniteTime(v float64) bool { return v >= 0 && v < 1e12 }
