      subroutine vumat(
     1 nblock, ndir, nshr, nstatev, nfieldv, nprops, lanneal,
     2 stepTime, totalTime, dt, cmname, coordMp, charLength,
     3 props, density, strainInc, relSpinInc,
     4 tempOld, stretchOld, defgradOld, fieldOld,
     5 stressOld, stateOld, enerInternOld, enerInelasOld,
     6 tempNew, stretchNew, defgradNew, fieldNew,
     7 stressNew, stateNew, enerInternNew, enerInelasNew )
c
c     Simplified rate-dependent skin damage material for Abaqus/Explicit.
c     Units expected by the model: mm, N, s, tonne, MPa.
c
c     props(1) = shear modulus, MPa
c     props(2) = bulk modulus, MPa
c     props(3) = viscosity-like stress coefficient, MPa*s
c     props(4) = accumulated equivalent strain at damage onset
c     props(5) = additional equivalent strain to near-complete failure
c
c     statev(1) = accumulated equivalent strain
c     statev(2) = scalar damage, 0..1
c     statev(3) = active flag for element deletion, 1 active, 0 deleted
c
      include 'vaba_param.inc'
c
      dimension props(nprops), density(nblock), coordMp(nblock,*),
     1 charLength(nblock), strainInc(nblock,ndir+nshr),
     2 relSpinInc(nblock,nshr), tempOld(nblock),
     3 stretchOld(nblock,ndir+nshr),
     4 defgradOld(nblock,ndir+nshr+nshr),
     5 fieldOld(nblock,nfieldv), stressOld(nblock,ndir+nshr),
     6 stateOld(nblock,nstatev), enerInternOld(nblock),
     7 enerInelasOld(nblock), tempNew(nblock),
     8 stretchNew(nblock,ndir+nshr),
     9 defgradNew(nblock,ndir+nshr+nshr),
     1 fieldNew(nblock,nfieldv), stressNew(nblock,ndir+nshr),
     2 stateNew(nblock,nstatev), enerInternNew(nblock),
     3 enerInelasNew(nblock)
c
      character*80 cmname
      parameter (zero=0.d0, one=1.d0, two=2.d0, three=3.d0)
c
      mu = props(1)
      bulk = props(2)
      eta = props(3)
      eps0 = props(4)
      epsf = props(5)
      if (epsf .le. 1.d-12) epsf = 1.d-12
c
      do k = 1, nblock
         active = stateOld(k,3)
         if (active .le. 0.5d0 .and. stateOld(k,2) .lt. 0.95d0)
     1      active = one
c
         if (active .le. 0.5d0) then
            do i = 1, ndir+nshr
               stressNew(k,i) = zero
            end do
            stateNew(k,1) = stateOld(k,1)
            stateNew(k,2) = one
            stateNew(k,3) = zero
            enerInternNew(k) = enerInternOld(k)
            enerInelasNew(k) = enerInelasOld(k)
         else
            tr = strainInc(k,1) + strainInc(k,2) + strainInc(k,3)
            e11 = strainInc(k,1) - tr/three
            e22 = strainInc(k,2) - tr/three
            e33 = strainInc(k,3) - tr/three
            eqinc = e11*e11 + e22*e22 + e33*e33
            if (nshr .ge. 1) eqinc = eqinc + two*strainInc(k,4)**2
            if (nshr .ge. 2) eqinc = eqinc + two*strainInc(k,5)**2
            if (nshr .ge. 3) eqinc = eqinc + two*strainInc(k,6)**2
            eqinc = sqrt(two*eqinc/three)
            eqeps = stateOld(k,1) + eqinc
c
            damOld = stateOld(k,2)
            dam = damOld
            if (eqeps .gt. eps0) then
               dam = (eqeps - eps0) / epsf
               if (dam .gt. one) dam = one
               if (dam .lt. damOld) dam = damOld
            end if
            scale = one - dam
            if (scale .lt. zero) scale = zero
c
            pressInc = bulk * tr
            do i = 1, ndir+nshr
               stressNew(k,i) = stressOld(k,i)
            end do
            stressNew(k,1) = stressNew(k,1) + two*mu*e11 + pressInc
            stressNew(k,2) = stressNew(k,2) + two*mu*e22 + pressInc
            stressNew(k,3) = stressNew(k,3) + two*mu*e33 + pressInc
            if (nshr .ge. 1) stressNew(k,4)=stressNew(k,4)
     1         + two*mu*strainInc(k,4)
            if (nshr .ge. 2) stressNew(k,5)=stressNew(k,5)
     1         + two*mu*strainInc(k,5)
            if (nshr .ge. 3) stressNew(k,6)=stressNew(k,6)
     1         + two*mu*strainInc(k,6)
c
            if (dt .gt. 1.d-18 .and. eta .gt. zero) then
               do i = 1, ndir+nshr
                  stressNew(k,i) = stressNew(k,i)
     1               + eta * strainInc(k,i) / dt
               end do
            end if
c
            do i = 1, ndir+nshr
               stressNew(k,i) = scale * stressNew(k,i)
            end do
c
            stateNew(k,1) = eqeps
            stateNew(k,2) = dam
            if (dam .ge. 0.98d0) then
               stateNew(k,3) = zero
               do i = 1, ndir+nshr
                  stressNew(k,i) = zero
               end do
            else
               stateNew(k,3) = one
            end if
c
            work = zero
            do i = 1, ndir+nshr
               work = work + 0.5d0*(stressOld(k,i)+stressNew(k,i))
     1              * strainInc(k,i)
            end do
            if (density(k) .gt. zero) then
               enerInternNew(k) = enerInternOld(k) + work/density(k)
            else
               enerInternNew(k) = enerInternOld(k)
            end if
            enerInelasNew(k) = enerInelasOld(k)
         end if
      end do
c
      return
      end
