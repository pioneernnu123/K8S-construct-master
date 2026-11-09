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

package labels

import (
	"slices"
	"strings"
)

const MetricName = "__name__"

type Label struct{ Name, Value string }
type Labels []Label

type ScratchBuilder struct {
	add       Labels
	unsafeAdd bool
}

// SymbolTable is no-op, just for api parity with dedupelabels.
type SymbolTable struct{}

func NewSymbolTable() *SymbolTable { return nil }

// NewScratchBuilder creates a ScratchBuilder initialized for Labels with n entries.
func NewScratchBuilder(n int) ScratchBuilder {
	return ScratchBuilder{add: make([]Label, 0, n)}
}

// NewBuilderWithSymbolTable creates a Builder, for api parity with dedupelabels.

// NewScratchBuilderWithSymbolTable creates a ScratchBuilder, for api parity with dedupelabels.
func NewScratchBuilderWithSymbolTable(_ *SymbolTable, n int) ScratchBuilder {
	return NewScratchBuilder(n)
}

// SetUnsafeAdd allows turning on/off the assumptions that added strings are unsafe
// for reuse. ScratchBuilder implementations that do reuse strings, must clone
// the strings.
//
// SliceLabels will clone all added strings when this option is true.
func (b *ScratchBuilder) SetUnsafeAdd(unsafeAdd bool) {
	b.unsafeAdd = unsafeAdd
}

func (b *ScratchBuilder) Reset() {
	b.add = b.add[:0]
}

// Add a name/value pair.
// Note if you Add the same name twice you will get a duplicate label, which is invalid.
// If SetUnsafeAdd was set to false, the values must remain live until Labels() is called.
func (b *ScratchBuilder) Add(name, value string) {
	if b.unsafeAdd {
		// Underlying label structure for slicelabels shares memory, so we need to
		// copy it if the input is unsafe.
		name = strings.Clone(name)
		value = strings.Clone(value)
	}
	b.add = append(b.add, Label{Name: name, Value: value})
}

// Sort the labels added so far by name.
func (b *ScratchBuilder) Sort() {
	slices.SortFunc(b.add, func(a, b Label) int { return strings.Compare(a.Name, b.Name) })
}

// Assign is for when you already have a Labels which you want this ScratchBuilder to return.
func (b *ScratchBuilder) Assign(ls Labels) {
	b.add = append(b.add[:0], ls...) // Copy on top of our slice, so we don't retain the input slice.
}

// Labels returns the name/value pairs added so far as a Labels object.
// Note: if you want them sorted, call Sort() first.
func (b *ScratchBuilder) Labels() Labels {
	// Copy the slice, so the next use of ScratchBuilder doesn't overwrite.
	return append([]Label{}, b.add...)
}
