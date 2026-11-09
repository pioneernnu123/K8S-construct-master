// Copyright Caleb Spare. BSD-2-Clause; see ../LICENSE.xxhash.
package xxhash

import (
	"encoding/binary"
	"math/bits"
)

const (
	prime1 uint64 = 11400714785074694791
	prime2 uint64 = 14029467366897019727
	prime3 uint64 = 1609587929392839161
	prime4 uint64 = 9650029242287828579
	prime5 uint64 = 2870177450012600261
)

// Store the primes in an array as well.
//
// The consts are used when possible in Go code to avoid MOVs but we need a
// contiguous array for the assembly code.
var primes = [...]uint64{prime1, prime2, prime3, prime4, prime5}

func Sum64(b []byte) uint64 {
	// A simpler version would be
	//   d := New()
	//   d.Write(b)
	//   return d.Sum64()
	// but this is faster, particularly for small inputs.

	n := len(b)
	var h uint64

	if n >= 32 {
		v1 := primes[0] + prime2
		v2 := prime2
		v3 := uint64(0)
		v4 := -primes[0]
		for len(b) >= 32 {
			v1 = round(v1, u64(b[0:8:len(b)]))
			v2 = round(v2, u64(b[8:16:len(b)]))
			v3 = round(v3, u64(b[16:24:len(b)]))
			v4 = round(v4, u64(b[24:32:len(b)]))
			b = b[32:len(b):len(b)]
		}
		h = rol1(v1) + rol7(v2) + rol12(v3) + rol18(v4)
		h = mergeRound(h, v1)
		h = mergeRound(h, v2)
		h = mergeRound(h, v3)
		h = mergeRound(h, v4)
	} else {
		h = prime5
	}

	h += uint64(n)

	for ; len(b) >= 8; b = b[8:] {
		k1 := round(0, u64(b[:8]))
		h ^= k1
		h = rol27(h)*prime1 + prime4
	}
	if len(b) >= 4 {
		h ^= uint64(u32(b[:4])) * prime1
		h = rol23(h)*prime2 + prime3
		b = b[4:]
	}
	for ; len(b) > 0; b = b[1:] {
		h ^= uint64(b[0]) * prime5
		h = rol11(h) * prime1
	}

	h ^= h >> 33
	h *= prime2
	h ^= h >> 29
	h *= prime3
	h ^= h >> 32

	return h
}
func u64(b []byte) uint64 { return binary.LittleEndian.Uint64(b) }

func u32(b []byte) uint32 { return binary.LittleEndian.Uint32(b) }

func round(acc, input uint64) uint64 {
	acc += input * prime2
	acc = rol31(acc)
	acc *= prime1
	return acc
}

func mergeRound(acc, val uint64) uint64 {
	val = round(0, val)
	acc ^= val
	acc = acc*prime1 + prime4
	return acc
}

func rol1(x uint64) uint64 { return bits.RotateLeft64(x, 1) }

func rol7(x uint64) uint64 { return bits.RotateLeft64(x, 7) }

func rol11(x uint64) uint64 { return bits.RotateLeft64(x, 11) }

func rol12(x uint64) uint64 { return bits.RotateLeft64(x, 12) }

func rol18(x uint64) uint64 { return bits.RotateLeft64(x, 18) }

func rol23(x uint64) uint64 { return bits.RotateLeft64(x, 23) }

func rol27(x uint64) uint64 { return bits.RotateLeft64(x, 27) }

func rol31(x uint64) uint64 { return bits.RotateLeft64(x, 31) }
