import os
import shutil
import numpy as np
from ase.atoms import Atoms
from TB2J.utils import symbol_number
from collections import defaultdict
from scipy.linalg import eigh
from TB2J.myTB import AbstractTB


class SislWrapper(AbstractTB):
    def __init__(self, sisl_hamiltonian, spin=None):
        self.is_siesta = False
        self.is_orthogonal = False
        self.ham = sisl_hamiltonian
        # k2Rfactor : H(k) = \int_R H(R) * e^(k2Rfactor * k.R)
        self.R2kfactor = 2.0j * np.pi  #
        if spin == 'up':
            spin = 0
        elif spin == 'down':
            spin = 1
        if spin not in [None, 0, 1, 'merge']:
            raise ValueError("spin should be None/0/1, but is %s" % spin)
        self.spin = spin
        self.orbs = []
        self.orb_dict = defaultdict(lambda: [])
        g = self.ham._geometry
        _atoms = self.ham._geometry._atoms
        atomic_numbers = []
        self.positions = g.xyz
        self.cell = np.array(g.sc.cell)
        for ia, a in enumerate(_atoms):
            atomic_numbers.append(a.Z)
        self.atoms = Atoms(numbers=atomic_numbers,
                           cell=self.cell,
                           positions=self.positions)
        sdict = list(symbol_number(self.atoms).keys())
        if self.ham.spin.is_colinear and (self.spin in [0, 1]):
            for ia, a in enumerate(_atoms):
                symnum = sdict[ia]
                try:
                    orb_names = [f"{symnum}|{x.name()}|up" for x in a.orbital]
                except:
                    orb_names = [f"{symnum}|{x.name()}|up" for x in a.orbitals]
                self.orbs += orb_names
                self.orb_dict[ia] += orb_names
            self.norb = len(self.orbs)
            self.nbasis = self.norb
        elif self.ham.spin.is_spinorbit or self.ham.spin.is_noncolinear or self.spin == 'merge':
            for ia, a in enumerate(_atoms):
                symnum = sdict[ia]
                orb_names = []
                for x in a.orbitals: 
                    for spin in {'up', 'down'}:
                        orb_names.append(f"{symnum}|{x.name()}|{spin}")
                self.orbs += orb_names
                self.orb_dict[ia] += orb_names
            self.norb = len(self.orbs) // 2
            self.nbasis = len(self.orbs)
        else:
            raise ValueError(
                "The hamiltonian should be either spin-orbit or colinear")
        self._name = 'SIESTA'


    def view_info(self):
        print(self.orb_dict)
        print(self.atoms)

    def solve(self, k, convention=2):
        if convention == 1:
            gauge = 'r'
        elif convention == 2:
            gauge = 'R'
        if self.spin in [0, 1]:
            evals, evecs = self.ham.eigh(k=k,
                                         spin=self.spin,
                                         eigvals_only=False,
                                         gauge=gauge)
        elif self.spin is None:
            evals, evecs = self.ham.eigh(k=k, eigvals_only=False, gauge=gauge)
        elif self.spin == 'merge':
            evals0, evecs0 = self.ham.eigh(k=k,
                                           spin=0,
                                           eigvals_only=False,
                                           gauge=gauge)
            evals1, evecs1 = self.ham.eigh(k=k,
                                           spin=1,
                                           eigvals_only=False,
                                           gauge=gauge)
            evals = np.zeros(self.nbasis, dtype=float)
            evecs = np.zeros((self.nbasis, self.nbasis), dtype=complex)
            evals[::2] = evals0
            evals[1::2] = evals1
            evecs[::2, ::2] = evecs0
            evecs[1::2, 1::2] = evecs1

        return evals, evecs

    def Hk(self, k, convention=2):
        if convention == 1:
            gauge = 'r'
        elif convention == 2:
            gauge = 'R'
        if self.spin is None:
            H = self.ham.Hk(k, gauge=gauge, format='dense')
        elif self.spin in [0, 1]:
            H = self.ham.Hk(k, spin=self.spin, gauge=gauge, format='dense')
        elif self.spin == 'merge':
            H = np.zeros((self.nbasis, self.nbasis), dtype='complex')
            H[::2, ::2] = self.ham.Hk(k,
                                                    spin=0,
                                                    gauge=gauge,
                                                    format='dense')
            H[1::2, 1::2] = self.ham.Hk(k,
                                                    spin=1,
                                                    gauge=gauge,
                                                    format='dense')
        return H

    def eigen(self, k, convention=2):
        return self.solve(k)

    def gen_ham(self, k, convention=2):
        return self.Hk(k, convention=convention)

    def Sk(self, k, convention=2):
        if convention == 1:
            gauge = 'r'
        elif convention == 2:
            gauge = 'R'
        S0 = self.ham.Sk(k, gauge='R', format='dense')
        if self.spin is None:
            S = S0
        elif self.spin in [0, 1]:
            S = S0
        elif self.spin == 'merge':
            S = np.zeros((self.nbasis, self.nbasis), dtype='complex')
            S[::2, ::2] = S0
            S[1::2, 1::2] = S0
        return S

    def solve_all(self, kpts, orth=False):
        evals = []
        evecs = []
        for ik, k in enumerate(kpts):
            if orth:
                S = self.Sk(k)
                Smh = Lowdin(S)
                H = self.gen_ham(k)
                Horth = Smh.T.conj() @ H @ Smh
                evalue, evec = eigh(Horth)
            else:
                evalue, evec = self.solve(k)
            evals.append(evalue)
            evecs.append(evec)
        return np.array(evals, dtype=float), np.array(evecs,
                                                      dtype=complex,
                                                      order='C')

    def HSE_k(self, k,convention=2):
        Hk = self.Hk(k, convention=convention)
        Sk = self.Sk(k, convention=convention)
        evalue, evec = self.solve(k, convention=convention)
        return Hk, Sk, evalue, evec


    def HS_and_eigen(self, kpts, convention=2):
        nkpts = len(kpts)
        evals = np.zeros((nkpts, self.nbasis), dtype=float)
        self.nkpts=nkpts
        if not self._use_cache:
            evecs = np.zeros((nkpts, self.nbasis, self.nbasis), dtype=complex)
            H = np.zeros((nkpts, self.nbasis, self.nbasis), dtype=complex)
            S = np.zeros((nkpts, self.nbasis, self.nbasis), dtype=complex)
        else:
            self._prepare_cache()

        for ik, k in enumerate(kpts):
            if self._use_cache:
                self.evecs = np.memmap(os.path.join(self.cache_path,
                                                    'evecs.dat'),
                                       mode='w+',
                                       shape=(nkpts, self.nbasis, self.nbasis),
                                       dtype=complex)
                self.H = np.memmap(os.path.join(self.cache_path, 'H.dat'),
                                   mode='w+',
                                   shape=(nkpts, self.nbasis, self.nbasis),
                                   dtype=complex)
                self.S = np.memmap(os.path.join(self.cache_path, 'H.dat'),
                                   mode='w+',
                                   shape=(nkpts, self.nbasis, self.nbasis),
                                   dtype=complex)
            self.H[ik] = Hk
            self.S[ik] = Sk
            self.evals[ik] = evalue
            self.evecs[ik] = evec
            if self._use_cache:
                del self.evecs
                del self.H
                del self.S
        return self.H, self.S, self.evals, self.evecs


    def _prepare_cache(self, path='./TB2J_results/cache'):
        if not os.path.exists(path):
            os.makedirs(path)
        else:
            os.remove(path)
        self.cache_path = path


    # def get_HSE_cached(self, kpt, convention=2):
    #     kpt = tuple(kpt)
    #     kname = f"{kpt[0]:9.5f}_{kpt[1]:9.5f}_{kpt[2]:9.5f}"
    #     if kname in self.cache_dict:
    #         path = self.cache_dict[kname]
    #         Hk = np.memmap(os.path.join(path, 'H.dat'),
    #                        dtype='complex',
    #                        mode='r',
    #                        shape=(self.nbasis, self.nbasis))
    #         Sk = np.memmap(os.path.join(path, 'H.dat'),
    #                        dtype='complex',
    #                        mode='r',
    #                        shape=(self.nbasis, self.nbasis))
    #         evalue = np.memmap(os.path.join(path, 'evalue.dat'),
    #                            dtype='float',
    #                            mode='r',
    #                            shape=(self.nbasis))
    #         evec = np.memmap(os.path.join(path, 'evec.dat'),
    #                          dtype='complex',
    #                          mode='r',
    #                          shape=(self.nbasis, self.nbasis))
    #     else:
    #         Hk, Sk, evalue, evec = self.get_HSE(kpt, convention=convention)
    #         path = os.path.join(self.cache_path, )
    #         self.cache_dict[tuple(kpt)] = path
    #         fpH = np.memmap(os.path.join(path, 'H.dat'))
    #     return Hk, Sk, evalue, evec

    def get_HSE(self, kpt, convention=2):
        Hk = self.Hk(kpt, convention=convention)
        Sk = self.Sk(kpt, convention=convention)
        evalue, evec = self.solve(kpt, convention=convention)
        return Hk, Sk, evalue, evec

    def get_fermi_level(self):
        return 0.0


#def test():
#    fdf = sisl.get_sile('/home/hexu/projects/learn_siesta/SMO_Wannier/siesta.fdf')
#    H = fdf.read_hamiltonian(order='nc',dim=2)
#    print(H._spin._is_polarized)
#    print(H.__dict__.keys())
#    print(H._geometry.__dict__.keys())
#    print(H._geometry.xyz)
#    print(H._geometry.sc)
#    H._geometry.sc.cell
#    orb=H._geometry._atoms[0].orbital[0]
#    print(orb.name())
#    s=SislWrapper(H)
#    s.view_info()
#
#import sisl
#fdf = sisl.get_sile('/home/hexu/projects/learn_siesta/SMO_Wannier/siesta.fdf')
#H = fdf.read_hamiltonian(order='nc',dim=2)
#if __name__=='__main__':
#    test()
