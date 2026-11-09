package monitor

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestTextFormatsAndLabels(t *testing.T) {
	at := time.Unix(1700000000, 0)
	for _, fixture := range []struct{ format, body string }{
		{"text/plain; version=0.0.4", "# TYPE rv_node_cpu_utilization_ratio gauge\nrv_node_cpu_utilization_ratio{note=\"a\\\"b\",job=\"wrong\"} 0.81\n"},
		{"application/openmetrics-text; version=1.0.0", "# TYPE rv_node_cpu_utilization_ratio gauge\nrv_node_cpu_utilization_ratio{note=\"a\\\"b\",job=\"wrong\"} 0.81\n# EOF\n"},
	} {
		samples, err := Parse([]byte(fixture.body), fixture.format, "node-a:9108", at, 100)
		if err != nil {
			t.Fatal(err)
		}
		if len(samples) != 1 || samples[0].Value != 0.81 || samples[0].Labels["note"] != "a\"b" || samples[0].Labels["job"] != "rv-nodes" || samples[0].Time != at.UnixMilli() {
			t.Fatalf("%+v", samples)
		}
	}
	clockBody := "rv_node_cpu_utilization_ratio 0.7\nrv_node_sample_timestamp_seconds 1699999998\n"
	samples, err := Parse([]byte(clockBody), "text/plain", "node-a", at, 10)
	if err != nil || samples[0].Time != 1699999998000 {
		t.Fatalf("sampling clock: %+v %v", samples, err)
	}
}

func TestMalformedAndLimitedScrapes(t *testing.T) {
	for _, body := range []string{
		"metric{label=\"unterminated} 1\n", "metric{a=\"b\",a=\"c\"} 1\n", "metric 1\nmetric 2\n", "metric invalid\n", "metric 1 999999999999999\n",
	} {
		if _, err := Parse([]byte(body), "text/plain", "n", time.Now(), 10); err == nil {
			t.Fatalf("accepted malformed body %q", body)
		}
	}
	if _, err := Parse([]byte("x 1\ny 2\n"), "text/plain", "n", time.Now(), 1); err == nil {
		t.Fatal("missing sample limit")
	}
	if _, err := Parse([]byte("x 1\n"), "application/x-protobuf", "n", time.Now(), 1); err == nil {
		t.Fatal("accepted unsupported protocol")
	}
}

func testSample(node string, stamp int64, value float64) Sample {
	return Sample{map[string]string{"__name__": "rv_node_cpu_utilization_ratio", "instance": node, "job": "rv-nodes"}, stamp, value}
}

func TestCompressedHistoryAggregationAndStaleness(t *testing.T) {
	s := NewStore(15*time.Minute, 100)
	base := time.Now().Add(-5 * time.Minute).Truncate(time.Millisecond)
	for i := 0; i < 260; i++ {
		at := base.Add(time.Duration(i) * time.Second)
		if err := s.Apply("a", []Sample{testSample("a", at.UnixMilli(), float64(i)/1000)}, at); err != nil {
			t.Fatal(err)
		}
	}
	at := base.Add(259 * time.Second)
	if err := s.Apply("b", []Sample{testSample("b", at.UnixMilli()-500, 0.9)}, at); err != nil {
		t.Fatal(err)
	}
	for expr, want := range map[string]float64{"max(rv_node_cpu_utilization_ratio)": 0.9, "min(rv_node_cpu_utilization_ratio)": 0.259, "sum(rv_node_cpu_utilization_ratio)": 1.159, "avg(rv_node_cpu_utilization_ratio)": 0.5795, "count(rv_node_cpu_utilization_ratio)": 2} {
		v, err := s.Query(expr, at, 45*time.Second, false)
		if err != nil || len(v) != 1 || math.Abs(v[0].Value-want) > 1e-12 {
			t.Fatalf("%s: %v %v", expr, v, err)
		}
		if v[0].Time != at.UnixMilli()-500 {
			t.Fatal("aggregate lost source timestamp")
		}
	}
	v, err := s.Query(`rv_node_cpu_utilization_ratio{instance=~"a|c",job!="wrong"}`, base.Add(129*time.Second), 45*time.Second, true)
	if err != nil || len(v) != 1 || v[0].Value != 0.129 {
		t.Fatalf("historical query: %+v %v", v, err)
	}
	if len(s.series[labelKey(testSample("a", 0, 0).Labels)].blocks) != 3 {
		t.Fatal("history did not cross XOR chunk boundaries")
	}
	s.Fail("b", at)
	v, _ = s.Query("max(rv_node_cpu_utilization_ratio)", at, 45*time.Second, false)
	if len(v) != 1 || v[0].Value != 0.259 {
		t.Fatal("failed target still contributes")
	}
	v, _ = s.Query("max(rv_node_cpu_utilization_ratio)", at.Add(time.Minute), 45*time.Second, false)
	if len(v) != 0 {
		t.Fatal("expired observations are visible")
	}
	if _, err := s.Query("rate(rv_node_cpu_utilization_ratio[5m])", at, time.Minute, false); err == nil {
		t.Fatal("unsupported query silently accepted")
	}
}

func TestStoreLimitsAndRetention(t *testing.T) {
	s := NewStore(time.Minute, 1)
	at := time.Now()
	if err := s.Apply("a", []Sample{testSample("a", at.UnixMilli(), 1)}, at); err != nil {
		t.Fatal(err)
	}
	if err := s.Apply("b", []Sample{testSample("b", at.UnixMilli(), 2)}, at); err == nil {
		t.Fatal("series limit missing")
	}
	if err := s.Apply("b", []Sample{testSample("b", at.Add(2*time.Minute).UnixMilli(), 2)}, at.Add(2*time.Minute)); err != nil {
		t.Fatal("expired series not reclaimed", err)
	}
}

func TestScrapeAndHTTPContract(t *testing.T) {
	var lock sync.Mutex
	status := 200
	body := "rv_node_cpu_utilization_ratio 0.85\n"
	exporter := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		lock.Lock()
		defer lock.Unlock()
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		w.WriteHeader(status)
		fmt.Fprint(w, body)
	}))
	defer exporter.Close()
	s, err := New(Config{Targets: []string{exporter.URL + "/metrics"}})
	if err != nil {
		t.Fatal(err)
	}
	ready := httptest.NewRecorder()
	s.Handler().ServeHTTP(ready, httptest.NewRequest("GET", "/-/ready", nil))
	if ready.Code != 503 {
		t.Fatal("ready before first sample")
	}
	s.scrape(context.Background(), 0)
	response := httptest.NewRecorder()
	s.Handler().ServeHTTP(response, httptest.NewRequest("GET", "/api/v1/query?query="+url.QueryEscape("max(rv_node_cpu_utilization_ratio)"), nil))
	var decoded struct {
		Status string
		Data   struct {
			ResultType string
			Result     []struct {
				Metric map[string]string
				Value  []any
			}
		}
	}
	if err := json.Unmarshal(response.Body.Bytes(), &decoded); err != nil {
		t.Fatal(err)
	}
	if response.Code != 200 || decoded.Status != "success" || decoded.Data.ResultType != "vector" || len(decoded.Data.Result) != 1 || decoded.Data.Result[0].Value[1] != "0.85" {
		t.Fatalf("unexpected API %s", response.Body.String())
	}
	for _, endpoint := range []string{"/-/ready", "/-/healthy", "/api/v1/targets"} {
		r := httptest.NewRecorder()
		s.Handler().ServeHTTP(r, httptest.NewRequest("GET", endpoint, nil))
		if r.Code != 200 {
			t.Fatalf("%s %d", endpoint, r.Code)
		}
	}
	for _, q := range []string{"rate(x[5m])", "x+1"} {
		r := httptest.NewRecorder()
		s.Handler().ServeHTTP(r, httptest.NewRequest("GET", "/api/v1/query?query="+url.QueryEscape(q), nil))
		if r.Code != 422 {
			t.Fatalf("query %s %d", q, r.Code)
		}
	}
	lock.Lock()
	status = 503
	lock.Unlock()
	s.scrape(context.Background(), 0)
	v, _ := s.Store.Query("max(rv_node_cpu_utilization_ratio)", time.Now(), time.Minute, false)
	if len(v) != 0 {
		t.Fatal("failed HTTP target retained current samples")
	}
}

func TestConcurrentReadersAndWrites(t *testing.T) {
	s := NewStore(time.Minute, 100)
	var workers sync.WaitGroup
	for n := 0; n < 8; n++ {
		workers.Add(1)
		go func(n int) {
			defer workers.Done()
			for i := 0; i < 100; i++ {
				at := time.Now()
				owner := fmt.Sprint(n)
				s.Apply(owner, []Sample{testSample(owner, at.UnixMilli(), float64(i))}, at)
				if _, err := s.Query("max(rv_node_cpu_utilization_ratio)", at, time.Minute, false); err != nil {
					t.Error(err)
				}
			}
		}(n)
	}
	workers.Wait()
}

func TestValidation(t *testing.T) {
	for _, c := range []Config{{}, {Targets: []string{"file:///tmp/x"}}, {Targets: []string{"host:9108"}, ScrapeInterval: "100ms"}, {Targets: []string{"host:9108"}, ScrapeTimeout: "30s"}, {Targets: []string{"host:9108", "host:9108"}}} {
		if _, err := New(c); err == nil {
			t.Fatalf("accepted invalid config %+v", c)
		}
	}
	if _, err := parseExpression(`rv_node_cpu_utilization_ratio{instance="node-a",job=~"rv-.*"}`); err != nil {
		t.Fatal(err)
	}
	if _, err := parseExpression(strings.Repeat("(", 100)); err == nil {
		t.Fatal("accepted malformed expression")
	}
}
