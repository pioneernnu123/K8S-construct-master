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

package exemplar

import "riscv.local/monitorcore/third_party/labels"

// ExemplarMaxLabelSetLength is defined by OpenMetrics: "The combined length of
// the label names and values of an Exemplar's LabelSet MUST NOT exceed 128
// UTF-8 characters."
// https://github.com/prometheus/OpenMetrics/blob/v1.0.0/specification/OpenMetrics.md#exemplars
const ExemplarMaxLabelSetLength = 128

// Exemplar is additional information associated with a time series.
type Exemplar struct {
	Labels labels.Labels `json:"labels"`
	Value  float64       `json:"value"`
	Ts     int64         `json:"timestamp"`
	HasTs  bool
}
