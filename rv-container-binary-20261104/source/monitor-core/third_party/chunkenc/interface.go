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

package chunkenc

// Float-only subset of upstream chunk interfaces and identifiers.
type Encoding uint8

const (
	EncNone Encoding = iota
	EncXOR
)

type ValueType uint8

const (
	ValNone ValueType = iota
	ValFloat
)

type Appender interface{ Append(st, t int64, v float64) }
type Iterator interface {
	Next() ValueType
	Seek(int64) ValueType
	At() (int64, float64)
	AtT() int64
	AtST() int64
	Err() error
}
