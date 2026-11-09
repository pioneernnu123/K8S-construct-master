package monitor

import (
	"fmt"
	"io"
	"math"
	"mime"
	"sort"
	"time"

	"riscv.local/monitorcore/third_party/labels"
	"riscv.local/monitorcore/third_party/textparse"
)

type Sample struct {
	Labels map[string]string
	Time   int64
	Value  float64
}

// Parse uses the attributed upstream parsers. It accepts float series in the
// Prometheus 0.0.4 and OpenMetrics 1.0 text formats, not native histograms.
func Parse(body []byte, contentType, instance string, at time.Time, maxSamples int) (result []Sample, err error) {
	defer func() {
		if p := recover(); p != nil {
			result = nil
			err = fmt.Errorf("malformed exposition: %v", p)
		}
	}()
	media := "text/plain"
	if contentType != "" {
		media, _, err = mime.ParseMediaType(contentType)
		if err != nil {
			return nil, err
		}
	}
	var parser textparse.Parser
	switch media {
	case "text/plain":
		parser = textparse.NewPromParser(body, nil, false)
	case "application/openmetrics-text":
		parser = textparse.NewOpenMetricsParser(body, nil)
	default:
		return nil, fmt.Errorf("unsupported metrics content type %q", media)
	}
	seen := make(map[string]bool)
	for {
		entry, nextErr := parser.Next()
		if nextErr == io.EOF {
			break
		}
		if nextErr != nil {
			return nil, nextErr
		}
		if entry != textparse.EntrySeries {
			continue
		}
		if len(result) >= maxSamples {
			return nil, fmt.Errorf("sample limit %d exceeded", maxSamples)
		}
		_, timestamp, value := parser.Series()
		t := at.UnixMilli()
		if timestamp != nil {
			t = *timestamp
		}
		if t > at.Add(5*time.Second).UnixMilli() {
			return nil, fmt.Errorf("sample timestamp is in the future")
		}
		var parsed labels.Labels
		parser.Labels(&parsed)
		ls := make(map[string]string, len(parsed)+2)
		for _, label := range parsed {
			if _, duplicate := ls[label.Name]; duplicate {
				return nil, fmt.Errorf("duplicate label %q", label.Name)
			}
			ls[label.Name] = label.Value
		}
		if ls["__name__"] == "" {
			return nil, fmt.Errorf("missing metric name")
		}
		// Target identity wins over an exporter-supplied job/instance label.
		ls["job"] = "rv-nodes"
		ls["instance"] = instance
		key := labelKey(ls)
		if seen[key] {
			return nil, fmt.Errorf("duplicate series within scrape")
		}
		seen[key] = true
		result = append(result, Sample{ls, t, value})
	}
	// The existing node agent exports its sampling clock. Preserve this clock
	// rather than making a frozen exporter look fresh on every HTTP poll.
	for _, s := range result {
		if s.Labels["__name__"] == "rv_node_sample_timestamp_seconds" {
			if math.IsNaN(s.Value) || math.IsInf(s.Value, 0) || s.Value < 0 || s.Value > float64(at.Add(5*time.Second).UnixMilli())/1000 {
				return nil, fmt.Errorf("invalid node sampling clock")
			}
			for i := range result {
				result[i].Time = int64(s.Value * 1000)
			}
			break
		}
	}
	return result, nil
}

func labelKey(ls map[string]string) string {
	names := make([]string, 0, len(ls))
	for name := range ls {
		names = append(names, name)
	}
	sort.Strings(names)
	key := ""
	for _, name := range names {
		key += fmt.Sprintf("%d:%s%d:%s", len(name), name, len(ls[name]), ls[name])
	}
	return key
}
