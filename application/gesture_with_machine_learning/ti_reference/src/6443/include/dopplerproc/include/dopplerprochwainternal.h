/*
* Copyright (C) 2024 Texas Instruments Incorporated
*
* All rights reserved not granted herein.
* Limited License.  
*
* Texas Instruments Incorporated grants a world-wide, royalty-free, 
* non-exclusive license under copyrights and patents it now or hereafter 
* owns or controls to make, have made, use, import, offer to sell and sell ("Utilize")
* this software subject to the terms herein.  With respect to the foregoing patent 
* license, such license is granted  solely to the extent that any such patent is necessary 
* to Utilize the software alone.  The patent license shall not apply to any combinations which 
* include this software, other than combinations with devices manufactured by or for TI ("TI Devices").  
* No hardware patent is licensed hereunder.
*
* Redistributions must preserve existing copyright notices and reproduce this license (including the 
* above copyright notice and the disclaimer and (if applicable) source code license limitations below) 
* in the documentation and/or other materials provided with the distribution
*
* Redistribution and use in binary form, without modification, are permitted provided that the following
* conditions are met:
*
*	* No reverse engineering, decompilation, or disassembly of this software is permitted with respect to any 
*     software provided in binary form.
*	* any redistribution and use are licensed by TI for use only with TI Devices.
*	* Nothing shall obligate TI to provide you with source code for the software licensed and provided to you in object code.
*
* If software source code is provided to you, modification and redistribution of the source code are permitted 
* provided that the following conditions are met:
*
*   * any redistribution and use of the source code, including any resulting derivative works, are licensed by 
*     TI for use only with TI Devices.
*   * any redistribution and use of any object code compiled from the source code and any resulting derivative 
*     works, are licensed by TI for use only with TI Devices.
*
* Neither the name of Texas Instruments Incorporated nor the names of its suppliers may be used to endorse or 
* promote products derived from this software without specific prior written permission.
*
* DISCLAIMER.
*
* THIS SOFTWARE IS PROVIDED BY TI AND TI'S LICENSORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, 
* BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. 
* IN NO EVENT SHALL TI AND TI'S LICENSORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR 
* CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, 
* OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, 
* OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE 
* POSSIBILITY OF SUCH DAMAGE.
*/

/**************************************************************************
 *************************** Include Files ********************************
 **************************************************************************/
#ifndef DOPPLERPROC_HWA_INTERNAL_H
#define DOPPLERPROC_HWA_INTERNAL_H

/* Standard Include Files. */
#include <stdint.h>
#include <stdlib.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

/* mmWave SDK Driver/Common Include Files */
#include <ti/drivers/hwa/hwa.h>
#include <ti/utils/cycleprofiler/cycle_profiler.h>

/* DPIF Components Include Files */
#include <ti/datapath/dpif/dpif_detmatrix.h>
#include <ti/datapath/dpif/dpif_radarcube.h>

/* mmWave SDK Data Path Include Files */
#include <ti/datapath/dpif/dp_error.h>
#include "include/dopplerproc/dopplerprochwa.h"

#ifdef __cplusplus
extern "C"
{
#endif

    /**
     * @brief
     *  dopplerProc DPU internal data Object
     *
     * @details
     *  The structure is used to hold dopplerProc internal data object
     *
     *  \ingroup DPU_DOPPLERPROC_INTERNAL_DATA_STRUCTURE
     */
    typedef struct DPU_DopplerProcHWA_Obj_t
    {
        /*! @brief HWA Handle */
        HWA_Handle hwaHandle;

        /*! @brief  EDMA driver handle. */
        EDMA_Handle edmaHandle;

        /*! @brief  EDMA configuration for Input data (Radar cube -> HWA memory). */
        DPU_DopplerProc_Edma edmaIn;

        /*! @brief HWA Processing Done semaphore Handle */
        SemaphoreP_Handle hwaDoneSemaHandle;

        /*! @brief EDMA Done semaphore Handle */
        SemaphoreP_Handle edmaDoneSemaHandle;

        /*! @brief Flag to indicate if DPU is in processing state */
        bool inProgress;

        /*! @brief  DMA trigger source channel for Ping param set */
        uint8_t hwaDmaTriggerSourcePing;

        /*! @brief  DMA trigger source channel for Pong param set */
        uint8_t hwaDmaTriggerSourcePong;

        /*! @brief  HWA number of loops */
        uint16_t hwaNumLoops;

        /*! @brief  HWA start paramset index */
        uint8_t hwaParamStartIdx;

        /*! @brief  HWA stop paramset index */
        uint8_t hwaParamStopIdx;

        /*! @brief  HWA DoA number of loops */
        uint16_t hwaDoANumLoops;

        /*! @brief  HWA DoA start paramset index */
        uint8_t hwaDoAParamStartIdx;

        /*! @brief  HWA DoA stop paramset index */
        uint8_t hwaDoAParamStopIdx;

        /*! @brief  HWA memory bank addresses */
        uint32_t hwaMemBankAddr[DPU_DOPPLERPROCHWA_NUM_HWA_MEMBANKS];

    } DPU_DopplerProcHWA_Obj;


#ifdef __cplusplus
}
#endif

#endif
