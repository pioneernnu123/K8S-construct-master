package monitor

import (
	"fmt"
	"sync"
	"time"

	"riscv.local/monitorcore/third_party/chunkenc"
)

type block struct {
	chunk *chunkenc.XORChunk
	app   chunkenc.Appender
	last  int64
}

type series struct {
	labels map[string]string
	owner  string
	active bool
	blocks []*block
	last   int64
}

type Store struct {
	mu        sync.RWMutex
	series    map[string]*series
	retention time.Duration
	maxSeries int
}

func NewStore(retention time.Duration, maxSeries int) *Store {
	return &Store{series: make(map[string]*series), retention: retention, maxSeries: maxSeries}
}

func (s *Store) pruneLocked(now int64) {
	cutoff := now - s.retention.Milliseconds()
	for key, item := range s.series {
		if item.last < cutoff {
			delete(s.series, key)
			continue
		}
		for len(item.blocks) > 1 && item.blocks[0].last < cutoff {
			item.blocks = item.blocks[1:]
		}
	}
}

// Apply is all-or-nothing at a target boundary. Missing/failed series are not
// returned by current queries; prior compressed samples remain for time queries.
func (s *Store) Apply(owner string, samples []Sample, at time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pruneLocked(at.UnixMilli())
	newCount := 0
	for _, sample := range samples {
		if sample.Time < at.Add(-s.retention).UnixMilli() {
			continue
		}
		if _, exists := s.series[labelKey(sample.Labels)]; !exists {
			newCount++
		}
	}
	if len(s.series)+newCount > s.maxSeries {
		return fmt.Errorf("series limit %d exceeded", s.maxSeries)
	}
	for _, item := range s.series {
		if item.owner == owner {
			item.active = false
		}
	}
	for _, sample := range samples {
		if sample.Time < at.Add(-s.retention).UnixMilli() {
			continue
		}
		key := labelKey(sample.Labels)
		item := s.series[key]
		if item == nil {
			ls := make(map[string]string, len(sample.Labels))
			for k, v := range sample.Labels {
				ls[k] = v
			}
			item = &series{labels: ls, owner: owner, last: -1}
			s.series[key] = item
		}
		item.active = true
		if sample.Time <= item.last {
			continue
		}
		if len(item.blocks) == 0 || item.blocks[len(item.blocks)-1].chunk.NumSamples() >= 128 {
			chunk := chunkenc.NewXORChunk()
			app, err := chunk.Appender()
			if err != nil {
				return err
			}
			item.blocks = append(item.blocks, &block{chunk: chunk, app: app})
		}
		b := item.blocks[len(item.blocks)-1]
		b.app.Append(0, sample.Time, sample.Value)
		b.last = sample.Time
		item.last = sample.Time
	}
	return nil
}

func (s *Store) Fail(owner string, at time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pruneLocked(at.UnixMilli())
	for _, item := range s.series {
		if item.owner == owner {
			item.active = false
		}
	}
}

func (s *Store) selectSamples(expr expression, at time.Time, lookback time.Duration, historical bool) ([]Sample, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := []Sample{}
	for _, item := range s.series {
		if (!historical && !item.active) || !expr.matches(item.labels) {
			continue
		}
		var best Sample
		found := false
		for i := len(item.blocks) - 1; i >= 0; i-- {
			it := item.blocks[i].chunk.Iterator(nil)
			for it.Next() != chunkenc.ValNone {
				t, v := it.At()
				if t > at.UnixMilli() {
					break
				}
				if t >= at.Add(-lookback).UnixMilli() {
					best = Sample{item.labels, t, v}
					found = true
				}
			}
			if err := it.Err(); err != nil {
				return nil, err
			}
			if found {
				break
			}
		}
		if found {
			result = append(result, best)
		}
	}
	return result, nil
}
