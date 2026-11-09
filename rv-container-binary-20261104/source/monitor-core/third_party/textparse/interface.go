// Copyright The Prometheus Authors
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package textparse

import (
	"riscv.local/monitorcore/third_party/exemplar"
	"riscv.local/monitorcore/third_party/labels"
	"riscv.local/monitorcore/third_party/model"
)

// Parser is the retained float/text portion of the upstream Parser interface.
type Parser interface {
	Series() ([]byte, *int64, float64)
	Help() ([]byte, []byte)
	Type() ([]byte, model.MetricType)
	Unit() ([]byte, []byte)
	Comment() []byte
	Labels(*labels.Labels)
	Exemplar(*exemplar.Exemplar) bool
	StartTimestamp() int64
	Next() (Entry, error)
}

// Entry represents the type of a parsed entry.
type Entry int

const (
	EntryInvalid   Entry = -1
	EntryType      Entry = 0
	EntryHelp      Entry = 1
	EntrySeries    Entry = 2 // EntrySeries marks a series with a simple float64 as value.
	EntryComment   Entry = 3
	EntryUnit      Entry = 4
	EntryHistogram Entry = 5 // EntryHistogram marks a series with a native histogram as a value.
)
