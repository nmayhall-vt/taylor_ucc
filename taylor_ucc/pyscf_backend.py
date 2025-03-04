import time
from pyscf import *
from pyscf import fci
from pyscf import cc
from pyscf import lo
from pyscf import mp
from pyscf.cc import ccsd_t
from pyscf.tools import molden
import numpy as np
from opt_einsum import contract
import copy
import psutil
import scipy

def compute_ao_F(H, I, C, nocc):
    #Computes F in the AO basis based on provided MO coefficients C and a.o. H, I.
    #I is in the chemist's notation.
    #Only necessary for ov rotations of the canonical MO's
    #Assumes RHF
    Ij = contract('pqrs,ru,st->pqut', I, C, C)
    J = contract('pqii->pq', Ij[:,:,:nocc,:nocc])
    Ik = contract('psrq,ru,st->ptuq', I, C, C)
    K = contract('piiq->pq', Ik[:,:nocc,:nocc,:])
    return H + 2*J - K

def compute_mo_F(H, I, C, nocc):
    #Computes F in the MO basis based on provided MO coefficients C and a.o. H, I.
    #I is in the chemist's notation.
    #Assumes RHF 
    I = contract('pqrs,pi,qj,rk,sl->ijkl', I, C, C, C, C)
    J = contract('pqii->pq', I[:,:,:nocc,:nocc])
    K = contract('piiq->pq', I[:,:nocc,:nocc,:])
    H = C.T.dot(H).dot(C)
    return H + 2*J - K
        
def semicanonicalize(H, I, C, nocc):
    #Semicanonicalizes F
    F = compute_mo_F(H, I, C, nocc)
    eo, vo = scipy.linalg.eigh(F[:nocc,:nocc])
    ev, vv = scipy.linalg.eigh(F[nocc:,nocc:])
    C[:,:nocc] = C[:,:nocc]@vo
    C[:,nocc:] = C[:,nocc:]@vv
    return C

def integrals(geometry, basis, reference, charge, unpaired, conv_tol, read = False, do_ccsd = True, do_ccsdt = True, chkfile = None, semi_canonical = False, manual_C = None):
    mol = gto.M(atom = geometry, basis = basis, charge = charge, spin = unpaired)
    print("\nSystem geometry:")
    print(geometry)
    mol.symmetry = False
    mem_info = psutil.virtual_memory()
    mol.max_memory = mem_info[1]
    mol.verbose = 4
    mol.build()
    if reference == "rhf":
        mf = scf.RHF(mol)
    else:
        print("Reference not understood.")
        exit()
    if chkfile is not None: 
        mf.chkfile = chkfile

    mf.direct_scf = True
    mf.direct_scf_tol = 0
    mf.max_cycle = 5000
    mf.conv_tol = copy.copy(conv_tol)
    mf.conv_tol_grad = copy.copy(conv_tol)
    mf.conv_check = True
    if read == True:
        mf.init_guess = 'chkfile'
    else: 
        mf.init_guess = 'atom'

    hf_energy = mf.kernel()
    
    assert mf.converged == True

    E_nuc = mol.energy_nuc()
    S = mol.intor('int1e_ovlp_sph')

    H_core = mol.intor('int1e_nuc_sph') + mol.intor('int1e_kin_sph')
    I = mol.intor('int2e_sph')
    mo_occ = copy.copy(mf.mo_occ)
    Oa = 0
    Ob = 0 
    Va = 0
    Vb = 0
    if reference == "rhf":
        mo_a = np.zeros(len(mo_occ))
        mo_b = np.zeros(len(mo_occ))

        for i in range(0, len(mo_occ)):
            if mo_occ[i] > 0:
                mo_a[i] = 1
                Oa += 1
            else:
                Va += 1
            if mo_occ[i] > 1:
                mo_b[i] = 1
                Ob += 1
            else:
                Vb += 1
            string = "MO energies"

        Ca = copy.copy(mf.mo_coeff)
        Cb = copy.copy(mf.mo_coeff)
        
    if manual_C is not None:
        Ca = copy.copy(manual_C)
        Cb = copy.copy(manual_C) 

    print(f"{Oa + Ob} electrons.")
    print(f"{Oa + Ob + Va + Vb} spin-orbitals.")
    Da = np.diag(mo_a) 
    Db = np.diag(mo_b)
    Ha = Ca.T.dot(H_core).dot(Ca)
    Hb = Cb.T.dot(H_core).dot(Cb)
    Iaa = contract('pqrs,pi,qj,rk,sl->ikjl', I, Ca, Ca, Ca, Ca)
    Iab = contract('pqrs,pi,qj,rk,sl->ikjl', I, Ca, Ca, Cb, Cb)
    Ibb = contract('pqrs,pi,qj,rk,sl->ikjl', I, Cb, Cb, Cb, Cb)
    Ja = contract('pqrs,qs->pr', Iaa, Da)+contract('pqrs,qs->pr', Iab, Db)
    Jb = contract('pqrs,qs->pr', Ibb, Db)+contract('pqrs,pr->qs', Iab, Da)
    Ka = contract('pqsr,qs->pr', Iaa, Da)
    Kb = contract('pqsr,qs->pr', Ibb, Db) 
    Fa = Ha + Ja - Ka
    Fb = Hb + Jb - Kb

    if semi_canonical == True :
        Ca = semicanonicalize(H_core, I, Ca, Oa)
        Cb = semicanonicalize(H_core, I, Cb, Ob)
        Fa = compute_mo_F(H_core, I, Ca, Oa)
        Fb = compute_mo_F(H_core, I, Cb, Ob)
        Ha = Ca.T.dot(H_core).dot(Ca)
        Hb = Cb.T.dot(H_core).dot(Cb)
        Iaa = contract('pqrs,pi,qj,rk,sl->ikjl', I, Ca, Ca, Ca, Ca)
        Iab = contract('pqrs,pi,qj,rk,sl->ikjl', I, Ca, Ca, Cb, Cb)
        Ibb = contract('pqrs,pi,qj,rk,sl->ikjl', I, Cb, Cb, Cb, Cb)

    manual_energy = E_nuc + .5*contract('pq,pq', Ha + Fa, Da) + .5*contract('pq,pq', Hb + Fb, Db)
    print(f"Canonical HF Energy (a.u.): {hf_energy:20.16f}")
    print(f"Reference Energy (a.u.):    {manual_energy:20.16f}")
    print(f"Energy Increase (a.u.):     {manual_energy-hf_energy:20.16f}")
    print(f"Largest Fa[o,v] term:       {np.amax(abs(Fa[:Oa,Oa:])):20.16e}")
    print(f"Norm of Fa[o,v] :       {np.linalg.norm(Fa[:Oa,Oa:]):20.16e}")
    delta_ref = manual_energy - hf_energy
    hf_energy = manual_energy
    vec_shape = (Va*Oa, Vb*Ob, int(Va*(Va-1)*Oa*(Oa-1)/4), Va*Vb*Oa*Ob, int(Vb*(Vb-1)*Ob*(Ob-1)/4))

    
    if do_ccsd == True or do_ccsdt == True and reference == 'rhf':
        try:
            start = time.time()
            mf.mo_coeff = copy.copy(Ca)
            mf.mo_energy = np.diag(Fa)
            mycc = cc.CCSD(mf, mo_coeff = Ca)
            mycc.max_cycle = 10000
            mycc.conv_tol = conv_tol
            mycc.verbose = 4        
            mycc.frozen = 0
            ccsd_energy = mycc.kernel(eris = mycc.ao2mo(mo_coeff = Ca))[0] + hf_energy 
            assert mycc.converged == True
            t1_norm = np.sqrt(2*contract('ia,ia->', mycc.t1, mycc.t1))
            t2_norm = np.sqrt(contract('ijab,ijab->', mycc.t2, mycc.t2))
            t1_diagnostic = cc.ccsd.get_t1_diagnostic(mycc.t1)
            d1 = cc.ccsd.get_d1_diagnostic(mycc.t1)
            d2 = cc.ccsd.get_d2_diagnostic(mycc.t2)
            print(f"CCSD T1 Norm:          {t1_norm:20.8e}")
            print(f"CCSD T1 Diagnostic:    {t1_diagnostic:20.8e}")
            print(f"CCSD D1 Diagnostic:    {d1:20.8e}")
            print(f"CCSD T2 Norm:          {t2_norm:20.8e}")
            print(f"CCSD D2 Norm:          {d2:20.8e}")
            print(f"CCSD Completed in {time.time() - start} seconds.")
            print(f"Converged CCSD Energy (a.u.): {ccsd_energy}")
        except:
            ccsd_energy = None
    else:
        ccsd_energy = None

    if do_ccsdt == True:
        try:
            start = time.time()
            correction = ccsd_t.kernel(mycc, mycc.ao2mo(), verbose = 4)
            ccsdt_energy = correction + mycc.e_tot
            print(f"CCSD(T) Completed in {time.time() - start} additional seconds.")
            print(f"Converged CCSD(T) Energy (a.u.): {ccsdt_energy}")
        except:
            ccsdt_energy = None
    else:
        ccsdt_energy = None



    return vec_shape, hf_energy, ccsd_energy, ccsdt_energy, Fa, Fb, Iaa, Iab, Ibb, Oa, Ob, Va, Vb, Ca, Cb


