package monitor

import (
	"fmt"
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

type matcher struct {
	name, op, value string
	re              *regexp.Regexp
}
type expression struct {
	metric, aggregate string
	filters           []matcher
}

var selectorRE = regexp.MustCompile(`^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?$`)
var matcherRE = regexp.MustCompile(`^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*("(?:[^"\\]|\\.)*")\s*(?:,\s*|$)`)

func parseExpression(query string) (expression, error) {
	e := expression{}
	query = strings.TrimSpace(query)
	for _, op := range []string{"max", "min", "avg", "sum", "count"} {
		prefix := op + "("
		if strings.HasPrefix(query, prefix) && strings.HasSuffix(query, ")") {
			e.aggregate = op
			query = strings.TrimSpace(query[len(prefix) : len(query)-1])
			break
		}
	}
	m := selectorRE.FindStringSubmatch(query)
	if m == nil {
		return e, fmt.Errorf("supported queries: metric{labels} or max/min/avg/sum/count(selector)")
	}
	e.metric = m[1]
	rest := strings.TrimSpace(m[2])
	for rest != "" {
		parts := matcherRE.FindStringSubmatch(rest)
		if parts == nil {
			return e, fmt.Errorf("invalid label matcher")
		}
		value, err := strconv.Unquote(parts[3])
		if err != nil {
			return e, err
		}
		f := matcher{name: parts[1], op: parts[2], value: value}
		if f.op == "=~" || f.op == "!~" {
			f.re, err = regexp.Compile("^(?:" + value + ")$")
			if err != nil {
				return e, err
			}
		}
		e.filters = append(e.filters, f)
		rest = strings.TrimSpace(rest[len(parts[0]):])
	}
	return e, nil
}

func (e expression) matches(ls map[string]string) bool {
	if ls["__name__"] != e.metric {
		return false
	}
	for _, f := range e.filters {
		value := ls[f.name]
		switch f.op {
		case "=":
			if value != f.value {
				return false
			}
		case "!=":
			if value == f.value {
				return false
			}
		case "=~":
			if !f.re.MatchString(value) {
				return false
			}
		case "!~":
			if f.re.MatchString(value) {
				return false
			}
		}
	}
	return true
}

func (s *Store) Query(query string, at time.Time, lookback time.Duration, historical bool) ([]Sample, error) {
	e, err := parseExpression(query)
	if err != nil {
		return nil, err
	}
	vector, err := s.selectSamples(e, at, lookback, historical)
	if err != nil {
		return nil, err
	}
	sort.Slice(vector, func(i, j int) bool { return labelKey(vector[i].Labels) < labelKey(vector[j].Labels) })
	if e.aggregate == "" || len(vector) == 0 {
		return vector, nil
	}
	t := vector[0].Time
	value := vector[0].Value
	for i, sample := range vector {
		if sample.Time < t {
			t = sample.Time
		}
		if i == 0 {
			continue
		}
		switch e.aggregate {
		case "max":
			if math.IsNaN(value) || sample.Value > value {
				value = sample.Value
			}
		case "min":
			if math.IsNaN(value) || sample.Value < value {
				value = sample.Value
			}
		case "sum", "avg":
			value += sample.Value
		}
	}
	if e.aggregate == "avg" {
		value /= float64(len(vector))
	}
	if e.aggregate == "count" {
		value = float64(len(vector))
	}
	// Returning source sample time is deliberate: the existing controller uses
	// this timestamp for freshness and consecutive-observation checks.
	return []Sample{{Labels: map[string]string{}, Time: t, Value: value}}, nil
}
